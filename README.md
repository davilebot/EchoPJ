# Plataforma Receita — Matcher de CNPJ

Aplicação independente para encontrar e validar CNPJs na base aberta da Receita Federal. O serviço compartilha a base oficial já existente no VPS, mas tem código, interface, autenticação, cache e implantação próprios. O Radar de Concessionárias não é alterado.

Aplicação publicada: `https://plataforma-receita-matcher.ztnbow.easypanel.host/`

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
