\set ON_ERROR_STOP on

-- Execute com: psql -v dataset_version=2026-08 -f build_branch_counts.sql
UPDATE rfb_aux_datasets
SET metadata=jsonb_set(metadata,'{branch_counts}','"building"')
WHERE version=:'dataset_version';

DELETE FROM rfb_company_branch_counts
WHERE dataset_version=:'dataset_version';

INSERT INTO rfb_company_branch_counts(
  dataset_version,cnpj_root,branch_count,active_branch_count
)
SELECT x.dataset_version,left(x.cnpj,8),count(*)::integer,
       count(*) FILTER (WHERE e.is_active)::integer
FROM rfb_establishment_details x
JOIN rfb_establishments e
  ON e.dataset_version=x.dataset_version AND e.cnpj=x.cnpj
WHERE x.dataset_version=:'dataset_version'
  AND x.branch_type_code='2'
GROUP BY x.dataset_version,left(x.cnpj,8);

ANALYZE rfb_company_branch_counts;

UPDATE rfb_aux_datasets
SET metadata=jsonb_set(metadata,'{branch_counts}','"ready"')
WHERE version=:'dataset_version';
