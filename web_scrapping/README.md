# Coleta da grade curricular (SIGAA/UFRN)

Este documento descreve como funciona o `coletar_grade.py`, que transforma a estrutura curricular pública de **Engenharia da Computação (UFRN, Natal)** em dois arquivos CSV: os **nós** (disciplinas) e as **arestas** (pré-requisito → disciplina) do grafo dirigido do projeto.

## 1. Entradas e saídas

| | |
|---|---|
| **Fonte** | Páginas públicas do SIGAA, curso `id=2000026`: `https://sigaa.ufrn.br/sigaa/public/curso/curriculo.jsf?lc=pt_BR&id=2000026` |
| **Entrada do usuário** | Qual estrutura curricular coletar (`--curriculo 03`, por exemplo). Sem a opção, usa a primeira marcada como *Ativa* |
| **Dependências** | `requests` (HTTP) e `beautifulsoup4` (HTML) |

Arquivos gerados na pasta `../dados/raw/` (ou na indicada em `--saida`):

| Arquivo | Conteúdo |
|---|---|
| `disciplinas.csv` | Uma linha por componente curricular da estrutura |
| `arestas.csv` | `pre_requisito,disciplina,alternativa` |
| `avisos.txt` | Tudo o que falhou, foi ignorado ou ficou fora do grafo |
| `bruto/` | HTML baixado (`lista.html`, `estrutura.html` e os primeiros `detalhe_*.html`), para depuração e reprocessamento (ignorado por .gitignore) |

## 2. Visão geral do fluxo

```
curriculo.jsf (lista de estruturas)
        │  escolhe a estrutura (--curriculo) e "clica" no ícone de visualizar
        ▼
Estrutura Curricular (disciplinas por nível)
        │  extrai código, nome, CH, natureza e nível de cada disciplina
        ▼
Página de detalhes de cada disciplina obrigatória
        │  lê os campos Pré-Requisitos / Co-Requisitos / Equivalências
        ▼
Expressão de pré-requisito  ──►  códigos citados  ──►  arestas
        │
        ▼
Validação (o grafo é um DAG?)  ──►  CSVs + avisos
```

## 3. Por que não basta um `requests.get`

O SIGAA é uma aplicação JSF (JavaServer Faces). Os ícones de "Visualizar Estrutura Curricular" e "Visualizar Detalhes do Componente" não são links comuns: têm `href="#"` e um `onclick` que chama a função JavaScript `jsfcljs`, a qual **envia um formulário (POST)** com parâmetros e o `javax.faces.ViewState`. Exemplo real do `onclick` da lista de currículos:

```js
jsfcljs(document.getElementById('formCurriculosCurso'),
        {'formCurriculosCurso:j_id_jsp_..._32':'formCurriculosCurso:j_id_jsp_..._32',
         'id':'171836029'}, '');
```

Para reproduzir o clique, o script faz exatamente o que o navegador faria: lê o `onclick`, junta os campos do formulário em volta do link (inclusive o `ViewState` oculto), acrescenta os parâmetros do `jsfcljs` e envia o POST. Isso é feito pelo método `Sigaa.seguir`.

## 4. Passo a passo no código

### 4.1 `Sigaa` (cliente HTTP)

- Mantém uma `requests.Session` (cookies de sessão do JSF) e um `User-Agent` identificando o uso acadêmico.
- `get` e `post` esperam `--atraso` segundos (padrão 1 s) antes de cada requisição, para não sobrecarregar o servidor.
- `sopa(r)` converte a resposta em BeautifulSoup usando a codificação declarada pelo servidor (ISO-8859-1).
- `seguir(a, pagina_url)` segue um link, tentando nesta ordem:
  1. `href` real (não `#` nem `javascript:`) → GET;
  2. URL do tipo `visualizarComponente/<id>` dentro do `onclick` → GET em `/sigaa/link/public/ensino/visualizarComponente/<id>`;
  3. chamada `jsfcljs` → POST do formulário. Aceita os parâmetros como **objeto JS** (formato real do SIGAA) ou como **string** `'chave,valor,chave,valor'` (formato clássico do MyFaces).

  Se nenhuma das três funciona, levanta `RuntimeError` mostrando o trecho do link.

### 4.2 Escolha da estrutura curricular (`listar_curriculos`, `abrir_estrutura`)

`listar_curriculos` percorre as linhas `<tr>` da tabela, reconhece o texto `Estrutura Curricular NN, Criado em AAAA`, o status (*Ativa*/*Inativa*) e o ícone com título "Visualizar Estrutura Curricular" (ignorando o "Relatório"). `abrir_estrutura` escolhe a estrutura pedida e usa `seguir` para abri-la. Há três caminhos de entrada:

| Opção | Comportamento |
|---|---|
| (padrão) / `--curriculo 03` | Lê a lista, escolhe e abre a estrutura |
| `--url-estrutura URL` | Pula a lista e abre uma URL direta de estrutura curricular |
| `--estrutura-html arquivo.html` | Não usa a rede: lê uma página salva pelo navegador |

### 4.3 Extração das disciplinas (`parse_estrutura`)

A página lista as disciplinas agrupadas por seções (`Optativas`, `Complementares`, `1º Nível`, `2º Nível`, …). Em vez de depender de uma estrutura HTML específica, o script:

1. extrai todo o texto visível da página e o junta em uma única linha;
2. percorre esse texto com uma expressão regular (`RE_TOKEN`) que reconhece, **na ordem em que aparecem**:
   - cabeçalho de nível: `N º Nível` → muda a seção atual para o nível N;
   - `Optativas` / `Complementares` → muda a seção atual;
   - item de disciplina: `CÓDIGO - NOME - 90h [Obrigatória|Optativa]`, com código no formato `[A-Z]{3}\d{4}[A-Z]?` (por exemplo `DCA0204`).

Cada item recebe o nível da seção em que apareceu e a natureza lida no texto (se ausente, deduzida da seção). Códigos repetidos são descartados.

Em seguida, cada ícone com título "Detalhes do Componente" é associado à sua disciplina: o script procura o texto com um código **imediatamente anterior** ao ícone no documento. O resultado é um dicionário `código → link`, usado na etapa seguinte.

### 4.4 Pré-requisitos (`parse_detalhe`)

A página de estrutura **não mostra pré-requisitos**; eles ficam na página de detalhes de cada componente. Para cada disciplina **obrigatória** (ou todas, com `--incluir-optativas`), o script abre a página de detalhes e lê, no texto, os campos `Pré-Requisitos:`, `Co-Requisitos:` e `Equivalências:`, sempre delimitando o valor pelo rótulo seguinte. Os textos são guardados **como estão** em `prerequisitos_brutos`, `corequisitos_brutos` e `equivalencias_brutas`, o que permite auditar e corrigir depois sem baixar tudo de novo.

São uma requisição e uma pausa por disciplina: com cerca de 50 obrigatórias, a coleta leva alguns minutos.

### 4.5 Construção das arestas

Para cada disciplina obrigatória D, com expressão de pré-requisito `expr`:

1. `codigos_da_expressao(expr)` extrai, por regex, todos os códigos citados (sem repetição);
2. para cada código P:
   - se `P == D`, é ignorado;
   - se P **não é disciplina obrigatória desta estrutura** (optativa, código de outra grade etc.), a aresta é ignorada e o fato vai para `avisos.txt`;
   - caso contrário, cria-se a aresta `P → D`.
3. A coluna `alternativa` vale **1** se a expressão contém a palavra `OU` (`tem_ou`), e **0** caso contrário.

Convenção de direção: a aresta aponta de quem **libera** para quem é **liberado**. Disciplinas sem pré-requisito não recebem arestas de entrada.

### 4.6 Validação (`ciclos`)

Como um pré-requisito não pode depender de si mesmo, o grafo deve ser um **DAG**. A função `ciclos` aplica o algoritmo de Kahn (remoção iterativa de nós com grau de entrada 0). Se algum nó sobra, há ciclo, e o script registra os nós restantes em `avisos.txt`. Atenção: a lista inclui os nós do ciclo **e os que dependem deles**, não só o ciclo em si. No terminal aparece `DAG válido: sim/NÃO`.

## 5. Formato dos CSVs

`disciplinas.csv`

| Coluna | Significado |
|---|---|
| `codigo` | Código SIGAA (`DCA0204`) |
| `nome` | Nome da disciplina |
| `ch` | Carga horária em horas |
| `natureza` | `Obrigatória`, `Optativa` ou `Complementar` |
| `nivel` | Nível (semestre ideal) da estrutura; vazio para optativas |
| `prerequisitos_brutos` | Texto original do campo Pré-Requisitos |
| `corequisitos_brutos` | Texto original do campo Co-Requisitos |
| `equivalencias_brutas` | Texto original do campo Equivalências |

`arestas.csv`

| Coluna | Significado |
|---|---|
| `pre_requisito` | Código de origem da aresta |
| `disciplina` | Código de destino |
| `alternativa` | 1 se a expressão de pré-requisito tinha `OU` |

Para o `networkx`, por exemplo: `nx.from_pandas_edgelist(arestas, "pre_requisito", "disciplina", create_using=nx.DiGraph)`, acrescentando os nós de `disciplinas.csv` para não perder as disciplinas isoladas.

## 6. Opções de linha de comando

| Opção | Efeito |
|---|---|
| `--listar` | Mostra as estruturas disponíveis e sai |
| `--curriculo 03` | Escolhe a estrutura pelo código |
| `--url-estrutura URL` | Usa uma URL direta de estrutura |
| `--estrutura-html f.html` | Lê uma página salva (sem rede, sem pré-requisitos) |
| `--sem-prereq` | Só gera `disciplinas.csv`; não abre as páginas de detalhes |
| `--incluir-optativas` | Também busca pré-requisitos das optativas |
| `--so-arestas` | Não acessa a rede: refaz `arestas.csv` a partir de `disciplinas.csv` (útil se você preencher `prerequisitos_brutos` à mão a partir do PPC) |
| `--atraso S` | Segundos entre requisições (padrão 1,0) |
| `--saida PASTA` | Pasta de saída (padrão `../dados/raw`, relativo ao script) |

## 7. Decisões de modelagem e limitações

- **Só disciplinas obrigatórias viram nós do grafo.** Optativas, atividades complementares e equivalências ficam fora do modelo e aparecem apenas no CSV de disciplinas.
- **Co-requisitos não geram arestas**, pois não são relação de precedência. Ficam guardados em texto bruto.
- **Lógica "A OU B":** cada alternativa vira uma aresta, e todas recebem `alternativa=1`. A marca vale para a expressão inteira, não por código: em `( A ) E ( B OU C )`, as arestas de A, B e C ficam marcadas, embora A seja obrigatório. Essa simplificação deve ser declarada como limitação no README.
- **Pré-requisitos por carga horária cumprida** (como "ter 1.500 h") não são capturados, pois não são códigos de disciplina.
- **Dependência do layout do SIGAA:** o script lê texto e rótulos (`Pré-Requisitos:`, `1º Nível`, `Visualizar Detalhes do Componente`). Se a UFRN mudar o site, algum desses pontos pode quebrar. O HTML baixado em `bruto/` serve para diagnosticar.
- **Estrutura escolhida:** cada estrutura (01/2012, 02/2020, 03/2024) tem sua própria grade. O resultado vale para a estrutura coletada, e isso deve constar no README.
- **Cuidado com a fonte:** os dados são públicos, e o script faz poucas requisições espaçadas. Registre nas referências a URL e a data da coleta.

## 8. Reprodutibilidade

```bash
pip install requests beautifulsoup4
python coletar_grade.py --listar
python coletar_grade.py --curriculo 03
```

Para o README do projeto (seção *Dados: fonte, como obter, limpeza*): fonte = páginas públicas do SIGAA citadas acima; obtenção = `coletar_grade.py`; limpeza = extração por regex dos códigos, descarte de pré-requisitos que não sejam obrigatórios da estrutura, registro dos descartes em `avisos.txt` e verificação de DAG.