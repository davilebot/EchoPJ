# Atualizacao automatica da Receita

O atualizador verifica diariamente o compartilhamento oficial em
https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9 e calcula a
competencia seguinte a que esta publicada em `dataset_versions`.

## Protecoes

1. Ausencia da proxima pasta e um estado normal: `waiting_for_release`.
2. A carga so e elegivel quando Empresas, Estabelecimentos e Socios possuem a
   mesma sequencia continua de pelo menos dez fragmentos; Simples e os seis
   dicionarios oficiais tambem precisam estar presentes.
3. O manifesto, incluindo os tamanhos oficiais, deve permanecer identico por
   18 horas. Isso evita iniciar durante o envio gradual da Receita.
4. Filas `queued` ou `running` nas duas aplicacoes adiam a carga.
5. O preflight reserva espaco para uma copia shadow completa, os ZIPs e 12 GB
   de margem. Falta de capacidade bloqueia antes do primeiro download.
6. Download e carga sao retomaveis. A base atual continua online durante todo
   o processamento.
7. A publicacao usa a validacao exata do importador e uma troca atomica. Depois
   dela, o PostgreSQL e as URLs internas das duas aplicacoes sao relidos.
8. O rollback anterior e mantido por no minimo sete dias. Quando habilitada, a
   aposentadoria automatica remove apenas as duas relacoes de rollback; ela nao
   apaga tabelas auxiliares ou dados operacionais.

O estado persistido fica em `/srv/echopjs-saas-v2/rfb-updater/status.json` e os
manifestos auditaveis em `/srv/echopjs-saas-v2/rfb-updater/manifests/`.

## Instalacao no VPS

1. Copiar `rfb-updater.env.example` para
   `/etc/easypanel/secrets/echopjs-rfb-updater/updater.env`, substituir somente
   o DSN administrativo e manter permissao `0600`.
2. Criar `/srv/echopjs-saas-v2/rfb-updater` e
   `/srv/radar-cnpj/tmp/rfb-auto-update` com acesso apenas ao operador.
3. Instalar `ops/systemd/echopjs-rfb-auto-update.service` e
   `ops/systemd/echopjs-rfb-auto-update.timer` em `/etc/systemd/system/`.
4. Executar primeiro a verificacao sem carga:

```bash
RFB_UPDATER_IMAGE=echopjs-saas:TAG docker compose \
  --project-name echopjs-saas_v2 \
  --file /opt/echopjs-saas-v2/compose.saas-v2.yml \
  --profile operations run --rm rfb-updater python scripts/auto_update_rfb.py --check-only
```

5. Habilitar o timer somente depois de validar `status.json`, as filas e o
   plano de capacidade:

```bash
systemctl enable --now echopjs-rfb-auto-update.timer
systemctl list-timers echopjs-rfb-auto-update.timer
```

O timer roda todos os dias as 03:40 em `America/Sao_Paulo`, com atraso aleatorio
de ate 20 minutos e execucao persistente caso o VPS esteja reiniciando no horario.

## Operacao

O comando abaixo mostra o ultimo resultado e o log da ultima tentativa:

```bash
jq . /srv/echopjs-saas-v2/rfb-updater/status.json
journalctl -u echopjs-rfb-auto-update.service -n 200 --no-pager
```

Estados sem incidente: `waiting_for_release`, `manifest_incomplete`,
`manifest_stabilizing`, `waiting_for_idle`, `importing` e `published`. `error`
exige diagnostico; a tentativa seguinte retoma o trabalho ja confirmado em banco.

## Compatibilidade e espaco permanente

A versao antiga da aplicacao ainda consulta quatro nomes auxiliares. O comando
`scripts/migrate_legacy_auxiliary_views.py` substitui o armazenamento duplicado
por views equivalentes sobre `rfb_establishments`. A migracao nunca apaga as
tabelas no primeiro passo:

```bash
python scripts/migrate_legacy_auxiliary_views.py --prepare
python scripts/migrate_legacy_auxiliary_views.py --cutover
```

Depois do `--cutover`, validar as duas URLs, busca, consulta por CNPJ e detalhe
de empresa. Se algo falhar, `--rollback` restaura as tabelas. Somente depois dos
smokes aprovados executar `--finalize`; esse ultimo passo libera o disco e nao
e reversivel sem uma nova importacao oficial.

Com as views validadas, `RFB_AUTO_RETIRE_ROLLBACKS=true` mantem apenas o rollback
da publicacao corrente por sete dias. Cada carga seguinte remove o rollback ja
vencido antes de reservar espaco para a nova tabela shadow.
