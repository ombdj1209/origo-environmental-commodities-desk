-- OTC Environmental Commodity Trade Capture — PostgreSQL 16 schema.
-- Applied once at API startup (skipped if `trades` already exists).

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE commodity_type_enum AS ENUM ('GOO', 'EUA', 'REGO');
CREATE TYPE trade_side_enum AS ENUM ('BUY', 'SELL');
CREATE TYPE unit_enum AS ENUM ('MWH', 'TCO2');
CREATE TYPE currency_enum AS ENUM ('EUR', 'GBP');
CREATE TYPE lifecycle_status_enum AS ENUM (
    'BOOKED',
    'CONFIRMATION_PENDING',
    'CONFIRMED',
    'REGISTRY_TRANSFER_INITIATED',
    'DELIVERED',
    'SETTLED',
    'RETIRED'
);
CREATE TYPE technology_enum AS ENUM (
    'HYDRO_RUN_OF_RIVER',
    'HYDRO_RESERVOIR',
    'SOLAR_PV',
    'WIND_ONSHORE',
    'WIND_OFFSHORE',
    'BIOMASS'
);
CREATE TYPE confirmation_status_enum AS ENUM (
    'GENERATED',
    'DISPATCHED',
    'ACKNOWLEDGED',
    'FAILED'
);

CREATE TABLE counterparties (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    legal_name VARCHAR(255) NOT NULL,
    lei VARCHAR(20) NOT NULL UNIQUE,
    country CHAR(2) NOT NULL,
    verticer_account_id VARCHAR(50),
    hknr_account_id VARCHAR(50),
    grexel_account_id VARCHAR(50),
    ofgem_account_id VARCHAR(50),
    union_registry_account_id VARCHAR(50),
    settlement_email VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_lei_structure CHECK (LENGTH(lei) = 20)
);

CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trade_reference VARCHAR(32) NOT NULL UNIQUE,
    commodity_type commodity_type_enum NOT NULL,
    counterparty_id UUID NOT NULL REFERENCES counterparties(id) ON DELETE RESTRICT,
    trader_id VARCHAR(64) NOT NULL,
    side trade_side_enum NOT NULL,
    price NUMERIC(12, 4) NOT NULL,
    volume NUMERIC(18, 3) NOT NULL,
    unit unit_enum NOT NULL,
    currency currency_enum NOT NULL,
    trade_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settlement_date DATE NOT NULL,
    lifecycle_status lifecycle_status_enum NOT NULL DEFAULT 'BOOKED',
    -- External references captured as the trade walks the lifecycle.
    registry_transfer_ref VARCHAR(128),
    settlement_reference VARCHAR(128),
    retirement_beneficiary VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_positive_price CHECK (price > 0),
    CONSTRAINT chk_positive_volume CHECK (volume > 0),
    CONSTRAINT chk_unit_alignment CHECK (
        (commodity_type = 'GOO' AND unit = 'MWH') OR
        (commodity_type = 'REGO' AND unit = 'MWH') OR
        (commodity_type = 'EUA' AND unit = 'TCO2')
    ),
    CONSTRAINT chk_currency_alignment CHECK (
        (commodity_type IN ('GOO', 'EUA') AND currency = 'EUR') OR
        (commodity_type = 'REGO' AND currency = 'GBP')
    )
);

CREATE TABLE goo_attributes (
    trade_id UUID PRIMARY KEY REFERENCES trades(id) ON DELETE CASCADE,
    vintage_start DATE NOT NULL,
    vintage_end DATE NOT NULL,
    technology technology_enum NOT NULL,
    country_of_origin CHAR(2) NOT NULL,
    eecs_domain VARCHAR(64) NOT NULL,
    is_supported BOOLEAN NOT NULL DEFAULT FALSE,
    commissioning_date DATE,
    issuing_body VARCHAR(128) NOT NULL,
    CONSTRAINT chk_vintage_chronology CHECK (vintage_start <= vintage_end)
);

CREATE TABLE eua_attributes (
    trade_id UUID PRIMARY KEY REFERENCES trades(id) ON DELETE CASCADE,
    compliance_year SMALLINT NOT NULL,
    surrender_phase VARCHAR(10) NOT NULL DEFAULT 'Phase IV',
    registry_account VARCHAR(100) NOT NULL,
    CONSTRAINT chk_phase_iv_compliance_year CHECK (compliance_year >= 2021 AND compliance_year <= 2030)
);

CREATE TABLE trade_confirmations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trade_id UUID NOT NULL REFERENCES trades(id) ON DELETE RESTRICT,
    document_hash CHAR(64) NOT NULL,
    document_path VARCHAR(512) NOT NULL,
    template_version VARCHAR(32) NOT NULL,
    dispatch_status confirmation_status_enum NOT NULL DEFAULT 'GENERATED',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    CONSTRAINT uq_trade_document_hash UNIQUE (trade_id, document_hash)
);

CREATE TABLE trade_audit_log (
    id BIGSERIAL PRIMARY KEY,
    trade_id UUID NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    action VARCHAR(64) NOT NULL,
    previous_state JSONB,
    new_state JSONB NOT NULL,
    performed_by VARCHAR(64) NOT NULL,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Blotter filtering and foreign key traversal.
CREATE INDEX idx_trades_counterparty ON trades(counterparty_id);
CREATE INDEX idx_trades_status ON trades(lifecycle_status);
CREATE INDEX idx_trades_timestamp ON trades(trade_timestamp DESC);
CREATE INDEX idx_trades_commodity_lifecycle ON trades(commodity_type, lifecycle_status);

-- Active operations pipeline (excludes historical closed trades).
CREATE INDEX idx_trades_active_pipeline ON trades(id, trade_reference, lifecycle_status)
WHERE lifecycle_status NOT IN ('SETTLED', 'RETIRED');

-- VWAP / technology spread aggregation.
CREATE INDEX idx_goo_tech_vintage ON goo_attributes(technology, vintage_start, vintage_end);

-- Audit traversal for historical state reconstruction.
CREATE INDEX idx_audit_trade_id_logged ON trade_audit_log(trade_id, logged_at DESC);

-- Seed counterparties so the desk is usable on first boot.
INSERT INTO counterparties (legal_name, lei, country, verticer_account_id, hknr_account_id,
                            grexel_account_id, ofgem_account_id, union_registry_account_id, settlement_email)
VALUES
  ('Vattenfall Energy Trading N.V.', '7245001LTG5SKXMOHE38', 'NL', 'NL-8712345000017', NULL, NULL, NULL, 'NL-100-12345-0-11', 'settlements@vattenfall-trading.example'),
  ('Statkraft Markets GmbH',         '529900W4QK6QAZQKGH64', 'DE', NULL, 'DE-HKNR-0042118', NULL, NULL, 'DE-121-98765-0-22', 'confirmations@statkraft-markets.example'),
  ('EDF Trading Limited',            '213800MBWEIJDM5CU638', 'FR', NULL, NULL, 'FR-GRX-500881', 'OFG-EDF-0071', 'FR-115-33221-0-31', 'ops@edftrading.example'),
  ('Shell Energy Europe B.V.',       '549300GAGL0F5EIL7O81', 'NL', 'NL-8712345000024', NULL, NULL, 'OFG-SEE-0099', 'NL-100-55443-0-44', 'gooops@shellenergy.example'),
  ('Axpo Solutions AG',              '506700GE1G29325QX363', 'CH', NULL, 'DE-HKNR-0077230', NULL, NULL, NULL, 'certificates@axpo.example')
ON CONFLICT (lei) DO NOTHING;
