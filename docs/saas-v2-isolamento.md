# Ambiente paralelo da EchoPJs SaaS

Esta branch cria a nova versao SaaS sem substituir a aplicacao atual. As duas
versoes podem rodar no mesmo VPS e consultar o mesmo PostgreSQL da Receita.

## Separacao obrigatoria

| Recurso | Aplicacao atual | EchoPJs SaaS |
| --- | --- | --- |
| Branch | `main` | `saas-v2` |
| Compose | `compose.vps.yml` | `compose.saas-v2.yml` |
| Imagem | `plataforma-receita-matcher:*` | `echopjs-saas:*` |
| Segredos | `/etc/easypanel/secrets/plataforma-receita/app.env` | `/etc/easypanel/secrets/echopjs-saas-v2/app.env` |
| Dados operacionais | `/srv/plataforma-receita/data` | `/srv/echopjs-saas-v2/data` |
| URL | URL atual | `https://echopjs-saas-v2.ztnbow.easypanel.host` |
| Receita Federal | PostgreSQL compartilhado, somente leitura | O mesmo PostgreSQL, somente leitura |

O compartilhamento termina no banco da Receita. Contas, sessoes, jobs, cache,
listas, creditos e pagamentos da v2 nunca devem usar o volume da aplicacao
atual. Quando esses dados migrarem de SQLite para PostgreSQL, a v2 deve usar um
banco ou schema operacional proprio.

Na fase inicial, `saas.sqlite` guarda perfis comerciais, livro-razao de creditos,
empresas desbloqueadas, listas e buscas salvas. O cache global de contatos e os
CPFs criptografados ficam em `partner-enrichment.sqlite`; o acesso aos resultados
continua isolado por organização. Os arquivos ficam apenas no volume
`/srv/echopjs-saas-v2/data`.

## Fluxo de desenvolvimento

Os dois diretorios locais sao worktrees do mesmo repositorio:

```text
plataforma-receita/       main, aplicacao atual
plataforma-receita-saas/  saas-v2, nova aplicacao
```

Correcoes solicitadas para a aplicacao atual entram primeiro em `main`. Quando
tambem forem validas para o SaaS, leve somente os commits necessarios:

```bash
git fetch origin main
git cherry-pick <commit-da-correcao>
```

Nao mescle `saas-v2` inteira em `main`. A troca de versao sera uma decisao de
implantacao tomada apenas depois da homologacao.

## Implantacao da v2 no EasyPanel

1. Use o projeto EasyPanel `echopjs-saas` e o Compose `v2`.
2. Cadastre um arquivo de segredos separado usando `saas-v2.env.example` como
   referencia e configure nele a nova `APP_PUBLIC_URL`.
3. Reutilize em `POSTGRES_DSN` a credencial somente leitura da Receita.
4. Nao copie `auth.sqlite`, `jobs.sqlite`, `partner-enrichment.sqlite` ou
   `website-cache.sqlite` da v1.
5. Construa uma imagem exclusiva e identificada pelo commit:

   ```bash
   git rev-parse --short HEAD
   docker build -t echopjs-saas:<commit> .
   ```

6. Defina `SAAS_IMAGE_TAG=<commit>` ao renderizar `compose.saas-v2.yml`.
7. Associe `https://echopjs-saas-v2.ztnbow.easypanel.host` ao servico
   `echopjs-saas_v2_app` na porta `8000`.
8. Execute o smoke test na nova URL e confirme que a URL atual continua servindo
   a imagem anterior.

## Protecoes contra troca acidental

- A v1 e a v2 possuem nomes de imagem, segredos, volumes e aliases diferentes.
- O Compose da v2 exige `SAAS_IMAGE_TAG`; nao existe uma tag implicita `latest`.
- O `APP_PUBLIC_URL` da v2 deve apontar para a URL nova antes de gerar convites.
- Alteracoes de schema necessarias ao SaaS devem ser feitas no banco operacional
  proprio. A base compartilhada da Receita continua somente leitura.
- O dominio atual nao sera alterado durante o desenvolvimento do SaaS.
