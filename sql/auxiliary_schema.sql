-- Complemento da base CNPJ. Este arquivo e somente aditivo: nao altera nem
-- bloqueia rfb_establishments, usada hoje pelo Radar e pelo matcher.

CREATE TABLE IF NOT EXISTS rfb_aux_datasets (
  version text PRIMARY KEY,
  source_url text,
  status text NOT NULL CHECK (status IN ('staging', 'current', 'ready', 'failed')),
  started_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  completed_at timestamptz,
  error text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS rfb_aux_one_current_idx
  ON rfb_aux_datasets ((status)) WHERE status = 'current';

CREATE TABLE IF NOT EXISTS rfb_aux_import_files (
  dataset_version text NOT NULL REFERENCES rfb_aux_datasets(version) ON DELETE CASCADE,
  file_name text NOT NULL,
  kind text NOT NULL,
  source_url text NOT NULL,
  status text NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  source_rows_processed bigint NOT NULL DEFAULT 0,
  rows_loaded bigint NOT NULL DEFAULT 0,
  bytes_downloaded bigint NOT NULL DEFAULT 0,
  started_at timestamptz,
  completed_at timestamptz,
  error text,
  PRIMARY KEY (dataset_version, file_name)
);

-- O nome, porte e capital social ja existem em rfb_establishments. Guardamos
-- aqui apenas os campos da empresa que ainda nao existem, evitando duplicacao.
CREATE TABLE IF NOT EXISTS rfb_company_details (
  dataset_version text NOT NULL REFERENCES rfb_aux_datasets(version) ON DELETE CASCADE,
  cnpj_root text NOT NULL CHECK (cnpj_root ~ '^[0-9A-Z]{8}$'),
  legal_nature_code text,
  responsible_qualification_code text,
  federative_entity text,
  PRIMARY KEY (dataset_version, cnpj_root)
);

CREATE TABLE IF NOT EXISTS rfb_establishment_details (
  dataset_version text NOT NULL REFERENCES rfb_aux_datasets(version) ON DELETE CASCADE,
  cnpj text NOT NULL CHECK (cnpj ~ '^[0-9A-Z]{14}$'),
  branch_type_code text,
  registration_status_reason_code text,
  foreign_city_name text,
  country_code text,
  phone1_area_code text,
  phone1 text,
  phone2_area_code text,
  phone2 text,
  fax_area_code text,
  fax text,
  email text,
  special_status text,
  special_status_date date,
  PRIMARY KEY (dataset_version, cnpj)
);

CREATE TABLE IF NOT EXISTS rfb_simples (
  dataset_version text NOT NULL REFERENCES rfb_aux_datasets(version) ON DELETE CASCADE,
  cnpj_root text NOT NULL CHECK (cnpj_root ~ '^[0-9A-Z]{8}$'),
  is_simples boolean,
  simples_started_at date,
  simples_ended_at date,
  is_mei boolean,
  mei_started_at date,
  mei_ended_at date,
  PRIMARY KEY (dataset_version, cnpj_root)
);

CREATE TABLE IF NOT EXISTS rfb_partners (
  dataset_version text NOT NULL REFERENCES rfb_aux_datasets(version) ON DELETE CASCADE,
  row_hash bytea NOT NULL CHECK (octet_length(row_hash) = 32),
  cnpj_root text NOT NULL CHECK (cnpj_root ~ '^[0-9A-Z]{8}$'),
  partner_type_code text,
  partner_type text,
  partner_name text,
  partner_document text,
  qualification_code text,
  joined_at date,
  country_code text,
  legal_representative_document text,
  legal_representative_name text,
  legal_representative_qualification_code text,
  age_range_code text,
  age_range text,
  PRIMARY KEY (dataset_version, row_hash)
);

CREATE INDEX IF NOT EXISTS rfb_partners_root_idx
  ON rfb_partners (dataset_version, cnpj_root);

-- Nao criamos indice pelo CPF/CNPJ do socio: a finalidade do produto e abrir
-- o quadro societario de uma empresa, e nao fazer busca reversa de pessoas.

CREATE TABLE IF NOT EXISTS rfb_aux_reference (
  dataset_version text NOT NULL REFERENCES rfb_aux_datasets(version) ON DELETE CASCADE,
  kind text NOT NULL,
  code text NOT NULL,
  label text NOT NULL,
  PRIMARY KEY (dataset_version, kind, code)
);

CREATE OR REPLACE VIEW rfb_current_company_details AS
  SELECT d.* FROM rfb_company_details d
  JOIN rfb_aux_datasets v ON v.version = d.dataset_version AND v.status = 'current';

CREATE OR REPLACE VIEW rfb_current_establishment_details AS
  SELECT d.* FROM rfb_establishment_details d
  JOIN rfb_aux_datasets v ON v.version = d.dataset_version AND v.status = 'current';

CREATE OR REPLACE VIEW rfb_current_simples AS
  SELECT d.* FROM rfb_simples d
  JOIN rfb_aux_datasets v ON v.version = d.dataset_version AND v.status = 'current';

CREATE OR REPLACE VIEW rfb_current_partners AS
  SELECT d.* FROM rfb_partners d
  JOIN rfb_aux_datasets v ON v.version = d.dataset_version AND v.status = 'current';

CREATE OR REPLACE VIEW rfb_current_aux_reference AS
  SELECT d.* FROM rfb_aux_reference d
  JOIN rfb_aux_datasets v ON v.version = d.dataset_version AND v.status = 'current';
