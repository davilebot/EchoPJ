# Atendimento do EchoPJs SaaS

O suporte da v2 funciona dentro da Central de Ajuda. Cada chamado pertence a
uma organização e fica visível para os integrantes desse workspace. A equipe
interna da EchoHub usa a fila do painel administrativo para responder e
atualizar o tratamento.

## Fluxo

1. O cliente escolhe categoria e prioridade, descreve o problema e abre o
   chamado.
2. O servidor anexa somente contexto operacional: identificador da requisição,
   versão da base, plano, status da assinatura e perfil de acesso. Senhas,
   filtros e empresas pesquisadas não fazem parte do diagnóstico.
3. A fila interna ordena primeiro prioridades urgentes e altas e depois os
   chamados abertos.
4. Uma resposta da EchoHub cria uma notificação para quem abriu o chamado.
5. Se o cliente responder a um chamado resolvido ou encerrado, ele volta para
   `open`.

## Estados

- `open`: recebido e aguardando triagem;
- `in_progress`: em atendimento pela EchoHub;
- `waiting_customer`: depende de informação ou ação do cliente;
- `resolved`: solução informada;
- `closed`: atendimento encerrado.

Um workspace suspenso continua autorizado a abrir e acompanhar chamados. Isso
evita que um problema comercial impeça o próprio contato necessário para
regularização.

## Procedimento operacional inicial

- Tratar prioridade `urgent` assim que ela aparecer na fila.
- Revisar chamados `open` antes dos demais estados.
- Registrar toda orientação importante na conversa do chamado.
- Usar `waiting_customer` quando faltar uma evidência objetiva.
- Marcar como `resolved` depois de informar a solução e como `closed` somente
  após concluir o atendimento.
