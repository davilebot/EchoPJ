# Proposta: colar vários CNAEs no seletor

**Estado:** publicada em produção  
**Data:** 23/10/2026

## Necessidade

Hoje, quem recebe uma lista de CNAEs para um segmento precisa pesquisar e marcar
cada código separadamente. O seletor já oferece busca por código ou atividade,
marcação individual e vários CNAEs selecionados. Essas interações devem continuar
funcionando.

## Comportamento proposto

Ao colar no campo de busca do seletor de CNAEs um bloco com linhas ou colunas,
o sistema identifica os códigos presentes e marca de uma vez os CNAEs
correspondentes. Isso permite copiar uma lista gerada pelo GPT ou uma tabela de
planilha, inclusive quando cada código vem acompanhado da descrição da
atividade.

- Aceitar códigos completos de sete dígitos, com ou sem a pontuação usual do
  CNAE (por exemplo, no formato `0000-0/00`), em linhas separadas ou em células
  separadas por tabulação. A extração deve funcionar mesmo com texto, cabeçalhos
  ou outras colunas ao redor dos códigos.
- Selecionar somente códigos encontrados nas opções de CNAE já carregadas da
  Receita. Códigos incompletos, desconhecidos ou outros números não entram na
  seleção.
- Acrescentar os códigos válidos à seleção existente, sem apagá-la e sem repetir
  um CNAE que já esteja marcado ou apareça mais de uma vez na colagem.
- Mostrar um retorno curto com quantos CNAEs foram acrescentados e quais códigos
  não puderam ser selecionados. Se nenhum código for válido, manter a seleção
  como estava.
- Manter a busca por nome ou código e a marcação por checkbox quando o usuário
  digitar ou colar uma consulta comum. A colagem em lote só deve ser acionada
  quando o conteúdo tiver estrutura de lista ou tabela.
- A colagem apenas altera os filtros selecionados; a busca de empresas continua
  dependendo da ação já existente para atualizar os resultados.

O mesmo comportamento deve valer nos quatro seletores de CNAE existentes:
incluir e excluir na busca de empresas, incluir e excluir no filtro de um lote de
CNPJs. Cada colagem atua somente no seletor em que foi feita. Os demais filtros
não mudam.

## Critérios para avaliar a implementação

1. Uma lista de códigos em linhas separadas marca todos os códigos válidos de
   uma vez.
2. Uma tabela com códigos e descrições em colunas também marca os códigos
   válidos, sem interpretar números de outras colunas como CNAE.
3. Uma segunda colagem acrescenta os novos códigos e ignora repetidos; as tags,
   o contador, o botão **Limpar** e a remoção individual continuam coerentes.
4. Códigos inválidos ou ausentes do catálogo são informados ao usuário e não
   entram nos filtros enviados à API.
5. Digitar e escolher sugestões continua funcionando como hoje. Uma colagem
   comum de termo único permanece no fluxo atual de pesquisa.
6. Os filtros salvos continuam usando os mesmos campos `cnaes` e
   `excluded_cnaes`. O limite existente da API de 2.000 códigos por campo deve
   ser respeitado com aviso explícito, sem corte silencioso.

## Referências no código atual

- Interface dos seletores: `service/static/index.html`.
- Componente de busca e seleção: `createMultiPicker` em `service/static/app.js`.
- Validação dos filtros de CNAE: `service/models.py`.

## Publicação e verificação

Publicada em 23/10/2026 na imagem `echopjs-saas:a2658f8`, derivada da imagem
anterior `echopjs-saas:9a9a5bb`. Somente `app.js`, `index.html` e `styles.css`
foram substituídos na imagem. O serviço oficial continua sendo
`echopjs-saas_v2-app-1`, no projeto Compose `echopjs-saas_v2`.

URL: https://echopjs-saas-v2.ztnbow.easypanel.host/

Verificações: 29 testes locais da interface passaram; o contêiner ficou
`healthy`; `GET /health/ready` respondeu HTTP 200 pela URL pública; e o
JavaScript servido teve o mesmo SHA-256 do arquivo testado localmente. A imagem
anterior `9a9a5bb` permanece disponível para reversão.
