-- Schema of the development database before Stage 9B-3 (after Stage 9B-2).
-- Captured read-only from rwa_legal_passport.db; used to test the 9B-3 migration.

CREATE TABLE "assets" (
	id VARCHAR(36) NOT NULL, 
	blockchain_network VARCHAR(50) NOT NULL, 
	stellar_asset_code VARCHAR(12) NOT NULL, 
	stellar_issuer_public_key VARCHAR(56) NOT NULL, 
	issuer_id VARCHAR(64) NOT NULL, 
	issuer_organization_name VARCHAR(255) NOT NULL, 
	asset_type VARCHAR(50) NOT NULL, 
	jurisdiction VARCHAR(100) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_stellar_asset UNIQUE (stellar_asset_code, stellar_issuer_public_key)
);

CREATE TABLE document_pages (
	id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	page_number INTEGER NOT NULL, 
	text_content TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
);

CREATE TABLE document_versions (
	id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	version_number INTEGER NOT NULL, 
	original_filename VARCHAR(255) NOT NULL, 
	stored_path VARCHAR(512) NOT NULL, 
	file_size INTEGER NOT NULL, 
	sha256_hash VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id)
);

CREATE TABLE documents (
	id VARCHAR(36) NOT NULL, 
	asset_id VARCHAR(36) NOT NULL, 
	document_type VARCHAR(50) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	current_version_number INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id)
);

CREATE TABLE legal_claims (
	id VARCHAR(36) NOT NULL, 
	extraction_id VARCHAR(36) NOT NULL, 
	field_name VARCHAR(50) NOT NULL, 
	value TEXT NOT NULL, 
	is_unknown BOOLEAN NOT NULL, 
	source_page INTEGER, 
	supporting_passage TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(extraction_id) REFERENCES legal_extractions (id)
);

CREATE TABLE legal_extractions (
	id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	ai_model VARCHAR(100) NOT NULL, 
	disclaimer TEXT NOT NULL, 
	extraction_timestamp DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
);

CREATE TABLE review_approvals (
	id VARCHAR(36) NOT NULL, 
	extraction_id VARCHAR(36) NOT NULL, 
	reviewer_id VARCHAR(100) NOT NULL, 
	reviewer_role VARCHAR(50) NOT NULL, 
	approval_type VARCHAR(50) NOT NULL, 
	disclaimer TEXT NOT NULL, 
	notes TEXT, 
	approved_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(extraction_id) REFERENCES legal_extractions (id)
);

CREATE TABLE review_corrections (
	id VARCHAR(36) NOT NULL, 
	extraction_id VARCHAR(36) NOT NULL, 
	claim_id VARCHAR(36) NOT NULL, 
	reviewer_id VARCHAR(100) NOT NULL, 
	reviewer_role VARCHAR(50) NOT NULL, 
	original_value TEXT NOT NULL, 
	original_is_unknown BOOLEAN NOT NULL, 
	original_passage TEXT, 
	corrected_value TEXT NOT NULL, 
	corrected_is_unknown BOOLEAN NOT NULL, 
	corrected_passage TEXT, 
	correction_note TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(extraction_id) REFERENCES legal_extractions (id), 
	FOREIGN KEY(claim_id) REFERENCES legal_claims (id)
);

CREATE INDEX ix_assets_issuer_id ON assets (issuer_id);

CREATE INDEX ix_document_pages_version_id ON document_pages (version_id);

CREATE INDEX ix_document_versions_document_id ON document_versions (document_id);

CREATE INDEX ix_documents_asset_id ON documents (asset_id);

CREATE INDEX ix_legal_claims_extraction_id ON legal_claims (extraction_id);

CREATE INDEX ix_legal_extractions_document_id ON legal_extractions (document_id);

CREATE INDEX ix_review_approvals_extraction_id ON review_approvals (extraction_id);

CREATE INDEX ix_review_corrections_extraction_id ON review_corrections (extraction_id);

CREATE UNIQUE INDEX uq_document_versions_doc_version ON document_versions (document_id, version_number);

CREATE UNIQUE INDEX uq_document_versions_stored_path ON document_versions (stored_path);
