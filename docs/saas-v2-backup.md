# Backup e recuperação do EchoPJs SaaS v2

Este procedimento cobre os dados operacionais exclusivos da v2: contas e
organizações (`auth.sqlite`), créditos/listas/buscas (`saas.sqlite`),
processamentos (`jobs.sqlite`) e o cache criptografado de enriquecimento de
sócios (`partner-enrichment.sqlite`). A base pública da Receita é compartilhada em
modo de leitura e segue o procedimento próprio do serviço de dados.

## Rotina local

O script `ops/backup_v2.py` usa a API de backup do SQLite, valida cada cópia com
`PRAGMA integrity_check`, grava hashes SHA-256 e só publica o diretório final
depois de verificar todos os arquivos. Um lock impede duas execuções ao mesmo
tempo. A retenção acontece apenas depois de um backup válido.

No VPS, o timer `echopjs-saas-v2-backup.timer` executa a rotina a cada seis
horas e mantém 56 cópias, equivalentes a aproximadamente 14 dias. Os diretórios
e arquivos usam permissões exclusivas do root. Para ver as últimas execuções:

```bash
systemctl list-timers echopjs-saas-v2-backup.timer
journalctl -u echopjs-saas-v2-backup.service -n 50 --no-pager
```

Para verificar uma cópia sem alterar a aplicação:

```bash
python3 /opt/echopjs-saas-v2/backup_v2.py \
  --verify /srv/echopjs-saas-v2/backups/backup-AAAAMMDDTHHMMSSZ
```

## Recuperação testável

1. Escolha a cópia desejada e execute `--verify`.
2. Pare somente `echopjs-saas_v2-app-1`.
3. Faça um último backup do estado atual e preserve os quatro arquivos existentes.
4. Copie `auth.sqlite`, `saas.sqlite`, `jobs.sqlite` e
   `partner-enrichment.sqlite` da cópia verificada para o
   volume `/srv/echopjs-saas-v2/data`.
5. Confirme proprietário `root:root`, modo `600` e execute `PRAGMA integrity_check`
   nos quatro arquivos.
6. Inicie somente a aplicação v2 e valide `/health`, login, organização, saldo,
   listas e histórico. A v1 não participa deste procedimento.

As cópias locais protegem contra migração defeituosa e corrupção acidental no
volume. Ainda é necessário configurar uma cópia criptografada fora do VPS para
cobrir perda total do servidor; esse destino depende da escolha operacional da
EchoHub.
