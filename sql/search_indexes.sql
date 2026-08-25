-- Preparado para ser executado pelo psql numa janela sem consultas.
-- NAO executar junto com a fila. A tabela e particionada por UF e o PostgreSQL
-- nao aceita CREATE INDEX CONCURRENTLY diretamente na tabela-pai. O \gexec
-- abaixo cria cada indice em uma particao, um comando por vez.

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s (uf,share_capital,cnpj) WHERE is_active AND share_capital IS NOT NULL',
  child.relname || '_active_capital_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s (cnpj_root,cnpj) WHERE is_active',
  child.relname || '_active_root_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s (uf,opened_at,cnpj) WHERE is_active AND opened_at IS NOT NULL',
  child.relname || '_active_opened_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s (uf,company_size,cnpj) WHERE is_active',
  child.relname || '_active_size_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s (postal_code,cnpj) WHERE is_active AND postal_code IS NOT NULL',
  child.relname || '_active_postal_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s (registration_status,cnpj)',
  child.relname || '_status_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec

SELECT format(
  'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s USING gin (secondary_cnaes) WHERE is_active AND secondary_cnaes IS NOT NULL',
  child.relname || '_active_secondary_cnaes_idx', child.oid::regclass
)
FROM pg_inherits inheritance
JOIN pg_class child ON child.oid = inheritance.inhrelid
WHERE inheritance.inhparent = 'rfb_establishments'::regclass
ORDER BY child.relname
\gexec
