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

## Teste de carga limitado

`ops/load_test_v2.py` mede três cenários sem criar listas, exportar dados ou
consumir créditos:

- `health`: prontidão completa da aplicação;
- `dashboard`: carregamento autenticado do workspace;
- `search`: busca por empresas ativas em SP, com prévia limitada a 20 linhas.

O utilitário aceita no máximo 20 requisições de busca e concorrência 20. A senha
é lida por variável de ambiente e não aparece no relatório. Exemplo:

```bash
export ECHOPJS_LOAD_PASSWORD='senha-da-conta-de-homologacao'
python ops/load_test_v2.py \
  --base-url https://echopjs-saas-v2.ztnbow.easypanel.host \
  --scenario dashboard --requests 40 --concurrency 4 \
  --username conta-de-homologacao@exemplo.com --organization-id 1
unset ECHOPJS_LOAD_PASSWORD
```

O processo termina com código diferente de zero se houver mais de 1% de erros
ou se o p95 ultrapassar 2 segundos. Para `search`, execute uma carga curta fora
do horário de pico e ajuste `--max-p95-ms` somente quando houver uma meta
documentada específica para a busca.

Alertas externos ainda exigem um destino operacional, como e-mail, Slack ou um
serviço de uptime. Até essa configuração, o Docker reinicia o processo quando o
healthcheck falha e o painel interno concentra o diagnóstico disponível.
