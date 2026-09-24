# Busca unificada da Receita

## Contrato de leitura

A busca operacional usa somente `rfb_establishments`, com uma linha por CNPJ.
Sócios continuam em `rfb_partners`, mas `partner_count` e
`partner_age_codes` ficam materializados na tabela mãe. Portanto, filtros e
contagens não consultam a relação de sócios.

O navegador executa duas chamadas sequenciais:

1. `POST /api/search` devolve até 10.000 linhas resumidas, ordenadas de forma
   determinística. A consulta pede 10.001 internamente para informar se existe
   continuação.
2. `POST /api/search/count` usa o mesmo construtor de predicados e devolve o
   total exato. Uma falha nessa segunda chamada não remove os resultados já
   carregados e pode ser repetida isoladamente.

Não existe rota de prévia nem limite intermediário de 500 registros.

## Construção e publicação

`scripts/build_unified_search.py` cria tabelas shadow identificadas pela versão
da Receita e registra o progresso em `rfb_unified_builds`. A etapa de empresas
é confirmada por UF; uma execução interrompida retoma da próxima UF.

Exemplo de preparação completa, sem publicar:

```bash
ADMIN_POSTGRES_DSN=... python scripts/build_unified_search.py --version 2026-08
```

A publicação só pode ocorrer depois da validação:

```bash
ADMIN_POSTGRES_DSN=... python scripts/build_unified_search.py --version 2026-08 --validate-only --publish
```

A troca renomeia as tabelas dentro de uma única transação, copia as permissões
de leitura e mantém as versões anteriores com sufixo `legacy_<versão>`. O
metadata da versão só passa a declarar `unified_search=ready` na mesma
transação; até lá, a aplicação continua usando o caminho legado.

Rollback:

```bash
ADMIN_POSTGRES_DSN=... python scripts/build_unified_search.py --version 2026-08 --rollback
```

As tabelas antigas só podem ser removidas após sete dias:

```bash
ADMIN_POSTGRES_DSN=... python scripts/build_unified_search.py --version 2026-08 --retire-legacy
```

## Próximas cargas mensais

Cada versão deve ser montada em staging e consolidada em novas tabelas shadow.
Não se altera a tabela operacional linha a linha. Depois de importar, criar os
índices, executar `ANALYZE` e validar paridade, publica-se por nova troca
atômica. O script usa nomes por versão para permitir que a tabela de rollback
do mês anterior continue existindo sem conflito.

