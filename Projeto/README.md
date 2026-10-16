# Projeto Unidade 1

## Problema

O currículo de Engenharia da Computação da UFRN é uma cadeia de dependências. Reprovar ou adiar uma disciplina pode atrasar várias outras, mas como a grade é em um formato de lista, não é possível observar quais disciplinas concentram esse risco.

## Pergunta

> Quais disciplinas são os pontos críticos do currículo, ou seja, as que mais bloqueiam o restante do curso, e qual é a cadeia de pré-requisitos mais longa, que define o tempo mínimo de formatura?

Para responder essa pergunta é necessário analisar a estrutura da rede, não apenas os atributos isolados de cada disciplina.

## Modelagem

| Elemento | Decisão |
| :---: | :---: |
| Nó | Uma disciplina obrigatória do currículo |
| Aresta | A -> B se A é pré-requisito de B |
| Direção | Dirigida, de quem libera para quem é liberado |
| Peso | Inicialmente sem peso |
| Fontes | Disciplinas sem pré-requisito têm grau de entrada 0 |

O que fica fora:
- Co-requisitos, porque não são relações de precedência.
- Optativas e atividades complementares.
- Equivalências.

**PROBLEMA**: No SIGAA, alguns pré-requisitos vêm com lógica "A ou B". A opção mais simples é criar uma aresta para cada alternativa e registrar essa simplificação como limitação.

## Análises possíveis

- Grau de saída: quantas disciplinas dependem diretamente de cada uma.
- Descendentes: quantas disciplinas ficam travadas se aquela for reprovada. Ponto crítico.
- Betweenness: disciplinas que ficam no meio dos caminhos, como pontes.
- Caminho mais longo: caminho crítico que define o mínimo de semestres.
- Comparação: verificar se as disciplinas críticas por estrutura coincidem com as do ciclo básico.

## Dados

Usar a estrutura curricular pública do curso no SIGAA e o PPC, que devem ser citados em “Referências”. Para um curso desse tamanho, algo na faixa de 50 a 60 nós, vale montar um CSV de arestas (pre_requisito, disciplina) e guardar também o script ou a descrição de como foi obtido. Isso cumpre a exigência de “dados, ou o script que os obtém”.