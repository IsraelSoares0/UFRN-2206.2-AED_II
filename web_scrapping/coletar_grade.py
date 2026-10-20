"""
coletar_grade.py - coleta a grade curricular publica do SIGAA/UFRN e gera CSVs
para modelar disciplinas como um grafo dirigido (pre-requisito -> disciplina).

Saidas (na pasta --saida, padrao ../dados/raw relativo a este script):
  disciplinas.csv   um no por disciplina (codigo, nome, ch, natureza, nivel, textos brutos)
  arestas.csv       pre_requisito,disciplina,alternativa   (alternativa=1 se a expressao tinha "OU")
  avisos.txt        o que nao foi possivel coletar ou ficou fora do grafo
  bruto/            HTML baixado (para reprocessar e para depurar)

Uso tipico:
  python coletar_grade.py --listar                                   # mostra os curriculos do curso
  python coletar_grade.py --curriculo 03                             # coleta a estrutura 03
  python coletar_grade.py --estrutura-html salvo.html --sem-prereq   # a partir de HTML salvo no navegador
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://sigaa.ufrn.br"
CURSO_ID = "2000026"  # Engenharia de Computacao / CT (Natal)
LISTA_URL = f"{BASE}/sigaa/public/curso/curriculo.jsf?lc=pt_BR&id={CURSO_ID}"
DETALHE_URL = BASE + "/sigaa/link/public/ensino/visualizarComponente/{id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (trabalho academico UFRN; coleta de pagina publica)"}

CODIGO = r"[A-Z]{3}\d{4}[A-Z]?"
RE_CODIGO = re.compile(CODIGO)
# jsfcljs(form, {'chave':'valor', ...}, '')    <- formato real do SIGAA (objeto JS)
# jsfcljs(form, 'chave,valor,chave,valor', '') <- formato classico do MyFaces (string)
RE_JSFCLJS = re.compile(r"jsfcljs\(\s*[^,]+,\s*(?:\{(?P<obj>.*?)\}|(?P<q>['\"])(?P<str>.*?)(?P=q))\s*,")
RE_PAR_OBJ = re.compile(r"['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]*)['\"]")

# Percorre o texto da pagina de estrutura curricular, na ordem, procurando:
# cabecalho de nivel ("1º Nível"), secoes "Optativas"/"Complementares" e itens
# no formato "CODIGO - NOME - 90h Obrigatória".
RE_TOKEN = re.compile(
    rf"(?P<nivel>(?P<n>\d+)\s*[º°]\s*N[ií]vel)"
    rf"|(?P<opt>\bOptativas\b)"
    rf"|(?P<comp>\bComplementares\b)"
    rf"|(?P<cod>\b{CODIGO})\s*-\s*(?P<nome>.+?)\s*-\s*(?P<ch>\d+)\s*h\b"
    rf"(?:\s*(?P<nat>Obrigat[óo]ria|Optativa|Complementar))?",
    re.I,
)

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Sigaa:
    def __init__(self, atraso=1.0):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.atraso = atraso

    def _ok(self, r):
        r.raise_for_status()
        return r

    def get(self, url):
        time.sleep(self.atraso)
        return self._ok(self.s.get(url, timeout=30))

    def post(self, url, dados):
        time.sleep(self.atraso)
        return self._ok(self.s.post(url, data=dados, timeout=30))

    @staticmethod
    def sopa(r):
        return BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding or "iso-8859-1")

    def seguir(self, a, pagina_url):
        """Segue um link do SIGAA: href normal, URL embutida no onclick ou submit JSF (jsfcljs)."""
        href = (a.get("href") or "").strip()
        onclick = a.get("onclick") or ""
        if href and not href.startswith(("#", "javascript")):
            return self.get(urljoin(pagina_url, href))
        
        m = re.search(r"visualizarComponente/(\d+)", onclick)
        if m:
            return self.get(DETALHE_URL.format(id=m.group(1)))
        
        m = RE_JSFCLJS.search(onclick)
        form = a.find_parent("form")
        if m and form:
            dados = {}
            for campo in form.find_all(["input", "select", "textarea"]):
                nome = campo.get("name")
                if not nome or (campo.get("type") or "").lower() in ("submit", "button", "image", "checkbox", "radio"):
                    continue
                dados[nome] = campo.get("value", "")
            if m.group("obj") is not None:
                dados.update(dict(RE_PAR_OBJ.findall(m.group("obj"))))
            else:
                pares = m.group("str").split(",")
                dados.update(dict(zip(pares[0::2], pares[1::2])))
            return self.post(urljoin(pagina_url, form.get("action") or pagina_url), dados)
        
        raise RuntimeError(f"Nao sei seguir este link: {str(a)[:300]}")

# ---------------------------------------------------------------------------
# PARSING
# ---------------------------------------------------------------------------
def texto_linhas(sopa):
    for t in sopa(["script", "style"]):
        t.decompose()
        
    return [l.strip() for l in sopa.get_text("\n").splitlines() if l.strip()]


def listar_curriculos(sopa):
    """Retorna [{'codigo','ano','status','ancora'}] a partir da pagina curriculo.jsf."""
    achados = []
    
    for tr in sopa.find_all("tr"):
        txt = " ".join(tr.get_text(" ").split())
        m = re.search(r"Estrutura Curricular\s+(\S+?),\s*Criado em\s+(\d{4})", txt)
        if not m:
            continue
        status = "Ativa" if re.search(r"\bAtiva\b", txt) else "Inativa"
        
        ancora = next((a for a in tr.find_all("a") if "Estrutura Curricular" in (a.get("title") or "")
                       and "Relat" not in (a.get("title") or "")), None)
        
        achados.append({"codigo": m.group(1), "ano": m.group(2), "status": status, "ancora": ancora})
        
    return achados


def parse_estrutura(sopa):
    """Extrai as disciplinas da pagina de Estrutura Curricular."""
    texto = " ".join(texto_linhas(sopa))
    secao = None  # int (nivel) | 'optativa' | 'complementar'
    itens, vistos = [], set()
    
    for m in RE_TOKEN.finditer(texto):
        if m.group("nivel"):
            secao = int(m.group("n"))
        elif m.group("opt"):
            secao = "optativa"
        elif m.group("comp"):
            secao = "complementar"
        else:
            cod = m.group("cod")
            if secao is None or cod in vistos:
                continue
            
            vistos.add(cod)
            nat = m.group("nat")
            
            if nat:
                nat = "Obrigatória" if nat.lower().startswith("obrigat") else nat.capitalize()
            else:
                nat = {"optativa": "Optativa", "complementar": "Complementar"}.get(secao, "Obrigatória")
            
            itens.append({
                "codigo": cod,
                "nome": m.group("nome").strip(),
                "ch": int(m.group("ch")),
                "natureza": nat,
                "nivel": secao if isinstance(secao, int) else "",
                "prerequisitos_brutos": "",
                "corequisitos_brutos": "",
                "equivalencias_brutas": "",
            })
            
    # liga cada item ao icone "Visualizar Detalhes do Componente"
    ancoras = {}
    for a in sopa.find_all("a"):
        if "Detalhes do Componente" in (a.get("title") or ""):
            prev = a.find_previous(string=RE_CODIGO)
            
            if prev:
                cod = RE_CODIGO.search(prev).group(0)
                ancoras.setdefault(cod, a)
                
    return itens, ancoras


def parse_detalhe(sopa):
    """Le Pre-Requisitos / Co-Requisitos / Equivalencias da pagina de detalhes do componente."""
    texto = " ".join(texto_linhas(sopa))

    def campo(rotulo, proximo):
        m = re.search(rf"{rotulo}\s*:\s*(.*?)\s*(?={proximo})", texto, re.S)
        return m.group(1).strip() if m else None

    pre = campo(r"Pr[ée]-?\s*Requisitos?", r"Co-?\s*Requisitos?\s*:")
    co = campo(r"Co-?\s*Requisitos?", r"Equival[êe]ncias?\s*:")
    eq = campo(r"Equival[êe]ncias?", r"Excluir da Avalia")
    
    return pre, co, eq


def codigos_da_expressao(expr):
    return list(dict.fromkeys(RE_CODIGO.findall(expr or "")))


def tem_ou(expr):
    return bool(re.search(r"\bOU\b", expr or ""))


def ciclos(nos, arestas):
    """Algoritmo de Kahn: devolve os nos que ficaram de fora."""
    grau = {n: 0 for n in nos}
    adj = {n: [] for n in nos}
    
    for a, b in arestas:
        adj[a].append(b)
        grau[b] += 1
        
    fila = [n for n, g in grau.items() if g == 0]
    visit = 0
    
    while fila:
        n = fila.pop()
        visit += 1
        for v in adj[n]:
            grau[v] -= 1
            if grau[v] == 0:
                fila.append(v)
                
    return [n for n, g in grau.items() if g > 0] if visit < len(nos) else []

# ---------------------------------------------------------------------------
# FLUXO
# ---------------------------------------------------------------------------
def abrir_estrutura(cli, args, bruto):
    """Devolve (sopa, url_da_pagina)."""
    if args.estrutura_html:
        html = Path(args.estrutura_html).read_bytes()
        return BeautifulSoup(html, "html.parser", from_encoding="iso-8859-1"), BASE + "/sigaa/"
    
    if args.url_estrutura:
        r = cli.get(args.url_estrutura)
    else:
        r = cli.get(LISTA_URL)
        (bruto / "lista.html").write_bytes(r.content)
        lista = listar_curriculos(cli.sopa(r))
        
        if not lista:
            sys.exit("Nao encontrei a tabela de curriculos. Veja bruto/lista.html (o layout pode ter mudado).")
        
        if args.listar:
            for c in lista:
                print(f"  {c['codigo']:>3}  {c['ano']}  {c['status']}")
            sys.exit(0)
        
        escolhido = next((c for c in lista if c["codigo"] == args.curriculo), None) if args.curriculo \
            else next((c for c in lista if c["status"] == "Ativa"), lista[0])
        
        if not escolhido or not escolhido["ancora"]:
            sys.exit(f"Curriculo '{args.curriculo}' nao encontrado ou sem link. Use --listar.")
        
        print(f"Curriculo escolhido: {escolhido['codigo']} ({escolhido['ano']}, {escolhido['status']})")
        r = cli.seguir(escolhido["ancora"], r.url)
    
    (bruto / "estrutura.html").write_bytes(r.content)
    return cli.sopa(r), r.url


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", default=str(Path(__file__).resolve().parent.parent / "dados" / "raw"),
                    help="pasta de saida (padrao: ../dados/raw relativo a este script)")
    ap.add_argument("--listar", action="store_true", help="so lista os curriculos disponiveis e sai")
    ap.add_argument("--curriculo", help="codigo da estrutura curricular (ex.: 03); padrao: a primeira Ativa")
    ap.add_argument("--url-estrutura", help="URL direta da pagina de Estrutura Curricular (pula a listagem)")
    ap.add_argument("--estrutura-html", help="arquivo HTML da estrutura salvo no navegador (pula a rede)")
    ap.add_argument("--sem-prereq", action="store_true", help="nao busca pre-requisitos (so gera disciplinas.csv)")
    ap.add_argument("--so-arestas", action="store_true",
                    help="nao usa a rede: refaz arestas.csv a partir de disciplinas.csv (preenchido a mao)")
    ap.add_argument("--incluir-optativas", action="store_true", help="tambem busca pre-requisitos das optativas")
    ap.add_argument("--atraso", type=float, default=1.0, help="segundos entre requisicoes (padrao 1.0)")
    args = ap.parse_args()

    saida = Path(args.saida)
    bruto = saida / "bruto"
    bruto.mkdir(parents=True, exist_ok=True)
    avisos = []
    cli = Sigaa(args.atraso)
    ancoras, url = {}, BASE

    if args.so_arestas:
        # plano B: le disciplinas.csv (escrita a mao) e refaz as arestas
        with open(saida / "disciplinas.csv", newline="", encoding="utf-8") as f:
            disciplinas = list(csv.DictReader(f))
            
        for d in disciplinas:
            d["nivel"] = int(d["nivel"]) if str(d["nivel"]).isdigit() else ""
            
        args.sem_prereq = True
    else:
        sopa, url = abrir_estrutura(cli, args, bruto)
        disciplinas, ancoras = parse_estrutura(sopa)
        
        if not disciplinas:
            sys.exit("Nenhuma disciplina reconhecida. Abra dados/raw/bruto/estrutura.html e confira o formato.")
    
    obrig = [d for d in disciplinas if d["natureza"] == "Obrigatória"]
    print(f"{len(disciplinas)} componentes ({len(obrig)} obrigatorios) em "
          f"{len({d['nivel'] for d in obrig if d['nivel']})} niveis.")

    # ---- pre-requisitos
    if not args.sem_prereq and not args.estrutura_html:
        alvo = disciplinas if args.incluir_optativas else obrig
        
        for i, d in enumerate(alvo, 1):
            a = ancoras.get(d["codigo"])
            
            if a is None:
                avisos.append(f"{d['codigo']}: sem link de detalhes na pagina.")
                continue
            try:
                r = cli.seguir(a, url)
                if i <= 3:
                    (bruto / f"detalhe_{d['codigo']}.html").write_bytes(r.content)
                pre, co, eq = parse_detalhe(cli.sopa(r))
            except Exception as e:  # noqa: BLE001
                avisos.append(f"{d['codigo']}: falha ao abrir detalhes ({e}).")
                continue
            if pre is None:
                avisos.append(f"{d['codigo']}: campo Pre-Requisitos nao encontrado na pagina de detalhes.")
                continue
            d["prerequisitos_brutos"], d["corequisitos_brutos"], d["equivalencias_brutas"] = pre, co or "", eq or ""
            print(f"  [{i}/{len(alvo)}] {d['codigo']}  <-  {pre or '(sem pre-requisito)'}")

    # ---- arestas
    nos = {d["codigo"] for d in obrig}
    arestas = []
    
    for d in obrig:
        expr = d["prerequisitos_brutos"]
        for cod in codigos_da_expressao(expr):
            if cod == d["codigo"]:
                continue
            
            if cod not in nos:
                avisos.append(f"{d['codigo']}: pre-requisito {cod} nao e obrigatorio desta estrutura (aresta ignorada).")
                continue
            
            arestas.append((cod, d["codigo"], 1 if tem_ou(expr) else 0))

    # ---- CSVs
    campos = ["codigo", "nome", "ch", "natureza", "nivel",
              "prerequisitos_brutos", "corequisitos_brutos", "equivalencias_brutas"]
    
    with open(saida / "disciplinas.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(disciplinas)
    
    with open(saida / "arestas.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pre_requisito", "disciplina", "alternativa"])
        w.writerows(arestas)

    # ---- validacao
    presos = ciclos(nos, [(a, b) for a, b, _ in arestas]) if arestas else []
    if presos:
        avisos.append("CICLO detectado entre: " + ", ".join(sorted(presos)))
    (saida / "avisos.txt").write_text("\n".join(avisos) + ("\n" if avisos else ""), encoding="utf-8")

    com_pre = {b for _, b, _ in arestas}
    
    print(f"\n{len(nos)} nos, {len(arestas)} arestas ({len(com_pre)} disciplinas com pre-requisito), "
          f"{sum(a[2] for a in arestas)} arestas marcadas como alternativa (OU).")
    
    print(f"DAG valido: {'sim' if not presos else 'NAO - veja avisos.txt'}")
    
    print(f"{len(avisos)} aviso(s) em {saida / 'avisos.txt'}")
    
    if not arestas and not args.so_arestas:
        print("\nNenhuma aresta foi obtida. Confira avisos.txt e dados/raw/bruto/detalhe_*.html. Plano B: preencha a "
              "coluna prerequisitos_brutos de disciplinas.csv (ex.: 'DCA0204 E ECT1203') com base no PPC e rode "
              "'python coletar_grade.py --so-arestas'.")


if __name__ == "__main__":
    main()