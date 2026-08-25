# Carga complementar da Receita

Esta carga amplia a base atual sem alterar `rfb_establishments`. O Radar e o
matcher continuam consultando a tabela atual durante toda a importacao.

## O que sera acrescentado

- empresa: natureza juridica, qualificacao do responsavel, porte, capital e ente federativo;
- estabelecimento: matriz/filial, motivo da situacao, pais/cidade no exterior,
  telefones, fax, e-mail e situacao especial;
- Simples Nacional e MEI, incluindo datas de entrada e saida;
- quadro de socios e administradores, com todos os campos publicos do arquivo;
- resumo por matriz com quantidade de filiais ativas e totais;
- dicionarios oficiais de CNAE, pais, natureza, municipio, qualificacao e motivo.

O nome, situacao e os dados completos dos estabelecimentos ativos ja estao na
base atual. A carga complementar preserva porte, capital, abertura, CNAEs e
endereco que a importacao anterior resumiu nos estabelecimentos nao ativos.

## Limite dos dados de socios

A Receita publica a faixa etaria, nao a idade exata nem a data de nascimento.
Documentos de pessoas fisicas chegam descaracterizados/mascarados e devem ser
preservados exatamente assim. O banco tem indice por CNPJ da empresa, mas nao
por documento do socio, evitando transformar a consulta empresarial em busca
reversa de pessoas.

## Protecoes operacionais

1. O importador exige acesso somente-leitura ao `jobs.sqlite` da plataforma.
2. Qualquer job `queued` ou `running` pausa a carga.
3. A carga tambem pausa quando o load do servidor passa do limite.
4. Cada ZIP e baixado em diretorio temporario e apagado ao terminar.
5. A copia e feita em blocos de 50 mil linhas, com retomada registrada.
6. Espaco livre e tamanho do PostgreSQL sao checados antes de cada bloco.
7. Uma trava do PostgreSQL impede dois importadores simultaneos.
8. A nova versao permanece invisivel ate todos os arquivos obrigatorios
   terminarem; a publicacao final e uma troca curta e atomica.
9. A contagem de filiais e calculada uma UF por vez antes da publicacao, para
   que as consultas nao precisem reagrupar todos os estabelecimentos.

## Ordem recomendada no dia da carga

1. Confirmar que nao existem consultas na fila.
2. Gerar e validar o manifesto da mesma competencia da base atual.
3. Medir o tamanho total dos ZIPs e estimar o pico de disco.
4. Aplicar apenas o schema aditivo.
5. Rodar primeiro os pequenos dicionarios, depois Empresas, Simples, Socios e,
   por ultimo, os complementos de Estabelecimentos.
6. Conferir contagens, amostras e a versao antes de publicar.
7. Liberar as novas colunas na API e na exportacao em uma implantacao separada.

## Comandos (somente quando a carga for autorizada)

O manifesto pode ser validado sem banco:

```bash
PYTHONPATH=src python scripts/plan_full_import.py caminho/manifest.json
```

Quando a fonte expuser um diretorio HTML, o manifesto e gerado sem baixar os
ZIPs:

```bash
PYTHONPATH=src python scripts/build_full_manifest.py \
  --version 2026-08 \
  --directory URL_DA_COMPETENCIA \
  --output caminho/manifest.json
```

A carga usa `POSTGRES_DSN` apenas no ambiente e nunca em arquivo versionado:

```bash
PYTHONPATH=src python -m plataforma_receita.rfb_importer \
  --manifest caminho/manifest.json \
  --jobs-db /data/jobs.sqlite
```

O processo do VPS deve ser iniciado com prioridade reduzida de CPU e disco
(`nice`/`ionice`) e com limites proprios de memoria e CPU no Docker.
