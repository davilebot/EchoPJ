# Plataforma Receita — Matcher de CNPJ

> **Desenvolvimento do EchoPJs SaaS:** esta worktree usa a branch `saas-v2` e
> possui implantacao separada. A aplicacao atual continua na branch `main`.
> Consulte [o guia de isolamento da v2](docs/saas-v2-isolamento.md) antes de
> configurar o EasyPanel ou compartilhar volumes e segredos.
> O [roteiro do produto SaaS](docs/saas-roadmap.md) registra o modelo de créditos,
> o que já está pronto e as próximas entregas comerciais.

Aplicação independente para encontrar e validar CNPJs na base aberta da Receita Federal. O serviço compartilha a base oficial já existente no VPS, mas tem código, interface, autenticação, cache e implantação próprios. O Radar de Concessionárias não é alterado.

Aplicação publicada: `https://plataforma-receita-matcher.ztnbow.easypanel.host/`

Para editar o código e configurar um ambiente local, comece pelo
[guia do desenvolvedor](docs/desenvolvimento.md). As configurações de exemplo
estão em [`.env.example`](.env.example).

## Primeira versão

- consulta individual por nome, endereço, município, UF e CEP;
- envio de CSV com até 10.000 empresas, preservando a ordem original;
- processamento assíncrono e sequencial, uma empresa por vez;
- retomada automática após reinício e histórico das últimas consultas;
- login próprio com sessão protegida, logout e área “Minha conta” para alterar
  o e-mail e a senha sem editar o VPS;
- download parcial durante o processamento ou final após a conclusão;
- resultado classificado como confirmado, revisão necessária ou não encontrado;
- download do CSV original acrescido do match, do cadastro completo da Receita
  e de todos os sócios na mesma linha;
- busca opcional e automática de CNPJ no HTML público do website;
- API direta `POST /api/matches/batch`, com até 200 entradas;
- fila persistente em `POST /api/jobs`, com até 10.000 entradas;
- busca direta por CNAE, região, UF, município, CEP, situação, porte, nome,
  capital social e data de abertura, com até 10.000 resultados por consulta;
- seletores pesquisáveis com múltiplos CNAEs e múltiplos municípios da UF
  escolhida, alimentados pela própria versão corrente da Receita;
- seleção múltipla de regiões, UFs, situações, portes, CEPs e faixas etárias
  dos sócios, com UFs limitadas às regiões marcadas e municípios às UFs;
- exclusão de até 100 nomes ou marcas, aplicada à razão social e ao nome
  fantasia sem interpolar texto do usuário no SQL;
- explorador da base com números de cobertura, andamento da carga complementar
  e ficha exata por CNPJ, sem liberar comandos SQL ao usuário;
- catálogo técnico somente leitura das tabelas e visões do PostgreSQL, com
  colunas, tipos e prévia limitada de registros reais;
- filtros preparados para Simples, MEI, natureza jurídica, matriz/filial,
  telefone e e-mail, liberados somente quando a carga complementar estiver
  na mesma versão da base principal;
- exportação da busca por filtros para CSV;
- quantidade digitável entre 1 e 10.000, com prévia antes de salvar o CSV;
- seleção manual de empresas na prévia ou download de todo o recorte;
- consulta exata de todos os estabelecimentos da mesma matriz pelo CNPJ-base;
- consulta exata em lote com até 10.000 CNPJs, preservação da ordem, prévia e CSV;
- quantidade de filiais ativas e totais na ficha, nas listas e nas exportações;
- filtros mínimo e máximo de filiais ativas usando resumo pré-calculado por matriz;
- porte traduzido pelo dicionário oficial e quadro societário completo na busca
  e nos CSVs, incluindo documento público mascarado e faixa etária;
- exportação única e uniforme no matcher, na consulta exata e na busca por filtros,
  com colunas dinâmicas para Sócio 1, Faixa Etária 1, Sócio 2, Faixa Etária 2
  e os demais dados públicos de cada sócio;
- busca genérica de possíveis unidades de redes pelo nome/marca, sempre
  distinguindo candidatos de uma relação oficial de franquia;
- somente empresas ativas por padrão.

## Contas, organizações e convites

- “Organizações e equipe” permite criar/renomear organizações, gerar convites,
  cancelar convites pendentes, alterar permissões e remover acessos.
- Administradores podem gerenciar apenas as organizações das quais são administradores.
  Também podem criar novas organizações, nas quais entram como administradores.
- Membros podem consultar e exportar na organização, mas não gerenciar pessoas.
- A conta existente é migrada uma única vez como administradora de “Minha organização”.
  Os jobs antigos são associados a ela sem alterar resultados, credenciais ou a Receita.
- O histórico, os arquivos e os downloads de jobs são isolados por organização.
  A base pública da Receita e a fila de execução continuam compartilhadas.
- A organização é explícita por aba: `X-Organization-Id` nas chamadas e
  `organization_id` nos downloads. A API revalida a participação em cada acesso.
  Clientes antigos sem contexto usam a primeira organização da conta.
- Convites são vinculados ao e-mail, expiram em 7 dias, usam token aleatório de uso único
  e só armazenam seu hash. O link usa fragmento para não expor o token em access logs.
- Uma conta existente precisa usar sua senha atual ao aceitar outro convite.
  Remover alguém não apaga a conta ou suas outras participações. O último administrador
  não pode ser removido/rebaixado. Alterações têm trilha de auditoria.
- Sem serviço de e-mail, use “Copiar convite” ou “Abrir no meu e-mail”. Envio automático
  exige `SMTP_HOST`, `SMTP_FROM` e, quando aplicável, `SMTP_USERNAME`/`SMTP_PASSWORD`
  no arquivo de segredos do servidor. Padrão: `SMTP_PORT=465`, `SMTP_SSL=true`;
  para STARTTLS use porta 587 e `SMTP_SSL=false`. Conexões sem TLS não são aceitas.
- `APP_PUBLIC_URL` define o endereço confiável dos links (padrão: endereço publicado).
  Credenciais SMTP nunca devem entrar no repositório.
- “Esqueci minha senha” envia um link de uso único, armazenado somente como hash,
  com validade padrão de 30 minutos (`AUTH_PASSWORD_RESET_MINUTES`). A troca invalida
  todas as sessões anteriores e a solicitação nunca revela se o e-mail possui conta.
- O cadastro público cria a conta e a organização somente depois da confirmação do
  e-mail. Ele permanece fechado por padrão; para abrir, configure SMTP e defina
  `SAAS_SELF_SIGNUP_ENABLED=true`. O link expira conforme
  `AUTH_SIGNUP_VERIFICATION_HOURS` e a nova organização recebe `SAAS_TRIAL_CREDITS`.
- Administradores da organização interna EchoHub acessam `/admin` para localizar
  empresas clientes, alterar plano e status, conceder ou descontar créditos e
  consultar o histórico de movimentações. A EchoHub permanece protegida como
  organização interna ativa com créditos ilimitados.
- O painel interno mede cadastro, ativação, entrada em plano comercial, uso em
  dias distintos e atividade nos últimos 30 dias. Eventos de produto guardam
  apenas o tipo da ação, identificadores técnicos e contagens; filtros e dados
  completos das empresas pesquisadas não são copiados para a telemetria.
- O construtor de listas oferece segmentos iniciais que respeitam os dados
  disponíveis na versão atual da Receita. A biblioteca também sugere modelos
  editáveis para prospecção prioritária, qualificação e acompanhamento.
- “Ajuda e suporte” reúne guias pesquisáveis, atalhos que abrem a ferramenta no
  workspace atual e um diagnóstico copiável com organização, plano, saldo e versão
  da base. O diagnóstico não inclui senha, sessão, filtros ou empresas pesquisadas.
- Operações pesadas possuem limite por organização, arquivos excessivos são recusados
  antes do processamento e cada workspace tem um teto de jobs simultâneos. Os valores
  podem ser ajustados por ambiente sem alterar os planos comerciais.

### Validação

Com as dependências de `requirements.txt` instaladas:
`PYTHONPATH=src python -m unittest discover -s tests`.
Os testes de API exercitam o ASGI completo com repositório da Receita simulado,
incluindo bloqueio de acesso entre organizações e exportações por URL adulterada.

## Regras de segurança do match

- CEP ou endereço isolado nunca confirma um CNPJ;
- coincidência de nome precisa ser sustentada por localização ou CNPJ direto;
- CNPJ extraído do site passa pelo dígito verificador e pela Receita;
- casos duvidosos ficam para revisão manual;
- são devolvidos score, confiança, sinais, três candidatos e versão da Receita.

## Desempenho medido no VPS

Com a versão `2026-08` e 72.617.105 estabelecimentos:

- 1 empresa: 26–32 ms no benchmark e 18 ms no teste funcional;
- 10 empresas: 4,2–4,5 s;
- 200 empresas: 52–59 s.

Em duas execuções consecutivas das primeiras 200 linhas, o resultado ficou estável em 44 confirmados, 93 para revisão e 63 sem resultado.

A consulta ao website é opcional, tem timeout, respeita `robots.txt` e usa cache de 30 dias. Os jobs e os resultados ficam em `/data/jobs.sqlite`, separados da base da Receita.

## Execução

O serviço está no projeto EasyPanel `plataforma-receita`, Compose `matcher`. O contêiner exige `POSTGRES_DSN`, `APP_USERNAME` e `APP_PASSWORD` no ambiente. No primeiro início, o login legado é importado para `/data/auth.sqlite`; depois disso, alterações feitas em “Minha conta” usam senha com hash e sessões persistentes. O arquivo `compose.vps.yml` reproduz a configuração publicada: conecta o serviço à rede privada da base compartilhada e a uma rede separada com saída para consultar websites.

O usuário PostgreSQL `plataforma_receita_ro` tem somente leitura. O contêiner roda com sistema de arquivos somente leitura, sem capabilities Linux e com limite de 1 GB de memória. Nenhum segredo deve ser salvo no repositório.

## Expansao da base oficial

O importador complementar de socios, Simples/MEI, contatos e dicionarios esta
preparado em `src/plataforma_receita/rfb_importer.py`. Ele e aditivo, possui
retomada e pausa automaticamente enquanto houver consultas na fila. O roteiro
operacional esta em `docs/carga-completa-receita.md`. Essa carga nao deve ser
iniciada enquanto a plataforma estiver processando jobs de usuarios.
