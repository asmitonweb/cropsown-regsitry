-- CropSown FARMER register (mirrors the Livestock Farmer register).
-- Register id fa120001-...-0001 ; farmer section written into the same register.

-- 1) Register definition
INSERT INTO "public"."g2p_register_definitions"
("register_id","register_mnemonic","register_subject","register_description","master_register_id","register_rank","functional_id_generation_required","register_purpose","program_id","program_mnemonic","register_icon","has_image","dedup_is_enabled","dedup_threshold_score","completion_score_required","outgest_applicable","requires_registrant_authentication","registrant_authentication_validity_days","registrant_re_auth_warning_days_before")
VALUES
('fa120001-0000-4000-8000-000000000001','Farmer','Farmers','Farmer Register',NULL,2,'FALSE','REGISTER',NULL,NULL,'','FALSE','FALSE',55,'FALSE','FALSE','FALSE',730,30)
ON CONFLICT ("register_id") DO NOTHING;

-- 2) Farmer identity section (data routed to the Farmer register via section_register_id)
INSERT INTO "public"."g2p_register_sections"
("register_id","section_id","section_register_id","is_core_section","section_mnemonic","section_description","documents_required","no_of_verifications_required","is_list","section_weightage","section_ui_schema","cr_auto_approve_for_bene_portal","cr_auto_approve_for_agent_portal","cr_auto_approve_for_staff_portal","cr_auto_approve_for_partner")
VALUES
('fa120001-0000-4000-8000-000000000001','cropsown_farmer_identity_section_01','fa120001-0000-4000-8000-000000000001','FALSE','cs_farmer_identity','Farmer Identity','FALSE',0,'FALSE',10,
'{"panels":[{"panels":[{"widgets":[{"widget":"text","widget-id":"farmer_id","widget-type":"input","widget-label":"Farmer ID","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.farmer_id"},{"widget":"text","widget-id":"fayda_fan_id","widget-type":"input","widget-label":"Fayda FAN ID","widget-required":true,"widget-data-path":"fa120001-0000-4000-8000-000000000001.fayda_fan_id"},{"widget":"text","widget-id":"first_name","widget-type":"input","widget-label":"First Name","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.first_name"}],"panel-id":"vp_cs_farmer_1","panel-orientation":"vertical"},{"widgets":[{"widget":"text","widget-id":"middle_name","widget-type":"input","widget-label":"Middle Name","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.middle_name"},{"widget":"text","widget-id":"last_name","widget-type":"input","widget-label":"Last Name","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.last_name"},{"widget":"select","widget-id":"gender","widget-type":"input","widget-label":"Gender","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.gender","widget-data-source":{"type":"static","options":[{"label":"MALE","value":"MALE"},{"label":"FEMALE","value":"FEMALE"},{"label":"OTHER","value":"OTHER"}]}}],"panel-id":"vp_cs_farmer_2","panel-orientation":"vertical"},{"widgets":[{"widget":"date","widget-id":"date_of_birth","widget-type":"input","widget-label":"Date of Birth","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.date_of_birth","widget-data-format":{"dateFormat":"DD/MM/YYYY","inputMethod":"picker","dateConstraint":"past-only"}},{"widget":"text","widget-id":"mobile_number","widget-type":"input","widget-label":"Mobile Number","widget-required":false,"widget-data-path":"fa120001-0000-4000-8000-000000000001.mobile_number"},{"widget":"date","widget-id":"registration_date","widget-type":"input","widget-label":"Registration Date","widget-required":false,"widget-data-default":"today","widget-data-path":"fa120001-0000-4000-8000-000000000001.registration_date","widget-data-format":{"dateFormat":"DD/MM/YYYY","inputMethod":"picker","dateConstraint":"past-only"}}],"panel-id":"vp_cs_farmer_3","panel-orientation":"vertical"}],"panel-id":"hp_cs_farmer","panel-orientation":"horizontal"}],"section-id":"cropsown_farmer_identity_section_01","section-title":"Farmer","section-editable":true}',
'FALSE','FALSE','FALSE','FALSE')
ON CONFLICT ("section_id") DO NOTHING;

-- 3) Intake captures the pair on its domain root; Farmer ADD is a separate CR.
-- Retired intake mapping fa120002-0000-4000-8000-000000000002 is inventoried,
-- never automatically deleted from an existing database.

-- 4) Register-view: a Farmer tab on the Farmer register + its section
INSERT INTO "public"."g2p_register_ui_tabs" ("tab_id","register_id","tab_label","tab_order","is_active")
VALUES ('cropsown_farmer_tab','fa120001-0000-4000-8000-000000000001','farmer',10,'TRUE')
ON CONFLICT ("tab_id") DO UPDATE SET "tab_label" = EXCLUDED."tab_label";

INSERT INTO "public"."g2p_register_ui_tab_sections" ("tab_section_id","register_id","tab_id","section_id","section_order")
VALUES ('fa120003-0000-4000-8000-000000000003','fa120001-0000-4000-8000-000000000001','cropsown_farmer_tab','cropsown_farmer_identity_section_01',10)
ON CONFLICT ("tab_section_id") DO NOTHING;

-- 5) Farmer list cards require configured display fields. Without this schema,
-- the native search response has display_fields=null and the pinned UI crashes.
-- Repair missing/empty defaults while retaining administrator customization.
INSERT INTO "public"."g2p_register_schemas"
("register_id", "deduplicate_schema", "search_result_schema", "filter_schema")
VALUES
('fa120001-0000-4000-8000-000000000001', '[]',
 '[{"field_name":"farmer_id","display_label":"Farmer ID","order":1},{"field_name":"gender","display_label":"Gender","order":2}]', '[]')
ON CONFLICT ("register_id") DO UPDATE SET
"search_result_schema" = CASE
    WHEN "g2p_register_schemas"."search_result_schema" IS NULL
      OR json_array_length("g2p_register_schemas"."search_result_schema") = 0
    THEN EXCLUDED."search_result_schema"
    ELSE "g2p_register_schemas"."search_result_schema"
END,
"deduplicate_schema" = COALESCE("g2p_register_schemas"."deduplicate_schema", EXCLUDED."deduplicate_schema"),
"filter_schema" = COALESCE("g2p_register_schemas"."filter_schema", EXCLUDED."filter_schema");

-- Crop Sown is the registry's working register: the staff portal opens the
-- lowest-ranked register first, so Farmer (a reference, entered through Crop
-- Sown intakes) ranks after it.
UPDATE "public"."g2p_register_definitions" SET "register_rank" = 2
WHERE "register_id" = 'fa120001-0000-4000-8000-000000000001' AND "register_rank" <> 2;
