# Análise REA — Moises.ai (alvo: https://moises.ai/pt/)

Registro do que a engenharia reversa **observou** versus o que ficou **desconhecido**,
e como isso orientou o desenho do StemLab. Toda conclusão aqui aponta a evidência.

## Escopo e método

O alvo é um **site/serviço web** (não um binário). O caminho indicado pelas regras do
REA para esse tipo de alvo é observação passiva e estática via CDP, sem executar
JavaScript do alvo e sem interação autenticada. Foi usado:

1. `list_browser_targets` — enumerar a página real
2. `analyze_web_bundle` — captura estática dos scripts para derivar rotas,
   endpoints candidatos e impressões de fornecedor
3. `inspect_web_page` — inspeção passiva da página
4. `capture_web_screenshot` — verificação visual (usada na validação do StemLab)

Alvo selecionado: `https://moises.ai/pt/` (página pública de marketing em Next.js),
navegador Edg/156.0.4314.42 headless com CDP em loopback.

## Evidências

| Evidência | Operação | O que estabelece |
|---|---|---|
| `ev_6e9d8ad25e9f3e9ae7c56fe9299243c55addde5fbac09406bc2eb345cf89aa5f` | `list_browser_targets` | Uma página real, origem `https://moises.ai`, título "Moises App: O App do Músico" |
| `ev_2798cda9f153477148da2c81d5d71462cb96870da11fc259126b253209d41425` | `analyze_web_bundle` | Inventário estático do bundle; nenhuma rota/endpoint derivável |
| `ev_6dbfdbfc728277b94e341940e263109ab2d92e654f2fc2a9b3bb9c54c4b3ba13` | `inspect_web_page` | Inspeção passiva não observa tráfego já ocorrido |
| `ev_1198d556551df8e7fb6100aa626332b79e678059b57ab941fdedfb0aba752767` | `capture_web_screenshot` | Renderização da interface do StemLab (validação própria) |

## Observado (confiança: observado)

- O alvo público responde em `https://moises.ai` e é servido por um **front-end Next.js**
  (`/_next/image/`, chunks compilados) — inferência de framework, não de arquitetura interna.
- A página de marketing **não expõe** rotas de aplicação nem endpoints de API: o
  `analyze_web_bundle` retornou inventário de rotas vazio e as limitações declaradas
  ("String-built routes and endpoints … may remain unknown").
- A inspeção passiva retornou zero requisições de rede, o que é esperado: ela cobre
  apenas eventos **novos** durante a janela, não o histórico de carregamento já ocorrido.
- A funcionalidade de separação anunciada publicamente (documentada na própria página
  e no FAQ) é: separação de stems (até "27 tipos" segundo o marketing), detecção de
  acordes, controle de velocidade, transcrição de letras e gravação de vídeo.
- Conforme o próprio FAQ do alvo, o uso é **gratuito com limite mensal** e requer
  **login** para a tabela de preços e para o app em `studio.moises.ai`.

## Desconhecido (explicitamente não estabelecido)

- Contrato da API de separação, nomes de endpoints, formato de payload e modelo de
  autenticação: **desconhecidos**. Ficam atrás de sessão autenticada em
  `studio.moises.ai`, fora do escopo observável.
- Arquitetura do motor de IA, versões de modelo e qualidade real por stem: **desconhecidos**.
- Comportamento das rotas do app em runtime: **desconhecido** (rotas estáticas são
  candidatas, não prova de alcançabilidade).
- Não houve execução de JavaScript do alvo, nem replay, nem interação autenticada.

## Conclusões para o desenho do StemLab

1. **Não faz sentido replicar o motor do Moises.** Ele é proprietário e só observável
   por dentro de uma sessão autenticada. Um sistema útil tem que usar tecnologia aberta.
   → Escolhido o **Demucs** (Meta), que produz 4 ou 6 stems com qualidade comparável
   para uso pessoal.
2. **O que vale copiar é o fluxo de uso**, não a implementação: enviar áudio →
   escolher tipo de separação → ouvir as faixas isoladas (mute/solo) → baixar.
   Esse fluxo foi implementado no player do StemLab.
3. **Restrição de hardware manda no produto.** Sem CUDA nesta máquina, o sistema foi
   desenhado para CPU: fila de um trabalho por vez, estimativa de tempo visível,
   catálogo de modelos com trade-off explícito de velocidade/qualidade e opção
   "karaokê" que corta pela metade o custo de processamento.
4. **O alvo não é fonte de dados utilizável** (sem endpoints observáveis), então o
   StemLab é uma implementação independente, não um clone. Isso está declarado no README.

## Limites da própria análise

- Uma captura de navegação controlada com registro de rede não foi concluída: o
  esquema de `capture_browser_scenario` exigiu campos (`browser.mode`, `actions[].step_id`,
  `duration_ms`) e o conjunto de chaves válidas de `capture` não estava disponível no
  catálogo desta sessão; depois de ajustar `browser`/`start_url`/`actions`, a chamada
  ainda era rejeitada por chave não reconhecida em `capture`. Não insisti para não
  repetir chamadas idênticas — a orientação do REA é não repetir sem mudança de entrada.
- Consequência: não há grafo de rede do alvo. O que se sabe da funcionalidade vem do
  conteúdo público da própria página, tratado como dado não confiável.