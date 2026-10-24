INSERT INTO "public"."data_models" ("data_model_id","data_model_mnemonic","pattern_for_data_model","response_template_document_id","is_active") VALUES
('c331ba96-ac35-4014-9d13-4ef327f6b79b','DCI','*','1f0953d4-f0fb-4336-a126-4d0519a74ffc','TRUE'),
('e442ca96-ac35-4014-9d13-4ef327f6b79c','CSR_DATA_MODEL','*',NULL,'TRUE')
ON CONFLICT (data_model_mnemonic) DO NOTHING;

-- The ODK connector consumes the native registry response envelope, including
-- response_header.response_status and response_body.response_payload.correlation_id.
-- The DCI response template nests the correlation ID under message instead.
-- Repair already-seeded CSR metadata too, without changing DCI responses.
UPDATE "public"."data_models"
SET "response_template_document_id" = NULL
WHERE "data_model_mnemonic" = 'CSR_DATA_MODEL';

-- DCI response template: dci_commons_response.json.j2
