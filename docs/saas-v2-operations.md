# Operação e observabilidade da v2

A aplicação distingue processo vivo de serviço pronto para receber clientes:

- `GET /health/live` confirma que o processo HTTP responde;
- `GET /health/ready` verifica PostgreSQL da Receita, bancos SQLite de contas,
  SaaS e processamentos, além do worker da fila;
- `GET /health` permanece como verificação compatível com o container atual;
- `GET /api/admin/operations` mostra o diagnóstico completo somente para
  administradores da organização interna EchoHub.

Todas as respostas recebem `X-Request-Id` e `X-Response-Time-Ms`. O processo
mantém em memória uma janela limitada de requisições com contagem de erros,
latência p50, p95 e p99 e rotas mais acessadas. O painel não armazena parâmetros,
corpos, e-mails, CNPJs, senhas ou tokens.

O backup publica `status.json` dentro do diretório de backups de forma atômica
depois de cada execução. Somente esse arquivo é montado como
`/data/backup-status.json` em modo de leitura no container da v2.
O painel considera o backup atrasado quando a última verificação bem-sucedida
tem mais de oito horas, cobrindo o intervalo de seis horas do timer e uma margem
operacional.

## Metas iniciais

- disponibilidade mensal da v2: 99,5%;
- `GET /health/ready` deve responder em até 3 segundos;
- erro HTTP 5xx na janela de cinco minutos: menor que 1%;
- latência p95 das rotas comuns autenticadas: menor que 2 segundos;
- backup verificado: no máximo oito horas de idade;
- restauração ensaiada: mensal e antes de migrações de dados relevantes.

Essas metas são o ponto de partida para a homologação. Busca ampla e
processamento em lote têm perfil diferente e devem ser medidos separadamente em
um teste de carga com uma cópia representativa da base.

Alertas externos ainda exigem um destino operacional, como e-mail, Slack ou um
serviço de uptime. Até essa configuração, o Docker reinicia o processo quando o
healthcheck falha e o painel interno concentra o diagnóstico disponível.
