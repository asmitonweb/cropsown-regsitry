-- Popup-only FIN verification. UID/FAN16 reference fields remain distinct.
CREATE OR REPLACE FUNCTION pg_temp.oan_popup_identity(node jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE result jsonb;
BEGIN
  IF jsonb_typeof(node)='array' THEN
    SELECT COALESCE(jsonb_agg(pg_temp.oan_popup_identity(value) ORDER BY ord),'[]'::jsonb)
      INTO result FROM jsonb_array_elements(node) WITH ORDINALITY AS a(value,ord);
  ELSIF jsonb_typeof(node)='object' THEN
    SELECT COALESCE(jsonb_object_agg(key,pg_temp.oan_popup_identity(value)),'{}'::jsonb)
      INTO result FROM jsonb_each(node);
    IF node->>'widget-data-path' = '6b06a95a-9a6c-5a33-a33d-c1625716c59c.foundational_id' THEN
      result := result || '{"widget":"display","widget-type":"display","widget-readonly":true,"widget-required":false}'::jsonb;
    END IF;
    IF jsonb_typeof(node->'widgets')='array'
       AND (node->'widgets')::text LIKE '%6b06a95a-9a6c-5a33-a33d-c1625716c59c.farmer_id%'
       AND NOT jsonb_path_exists(node->'widgets','lax $.** ? (@."widget" == "oan-identity-verification")') THEN
      result := jsonb_set(result,'{widgets}',result->'widgets' || jsonb_build_array('{"widget": "oan-identity-verification", "widget-id": "identity_verification", "widget-type": "input", "widget-label": "Fayda verification", "widget-readonly": false, "widget-required": false, "widget-data-path": {"foundationalId": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.fayda_fin", "verificationId": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.identity_verification_id", "status": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.last_authentication_status", "verifiedOn": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.last_authenticated_on", "expiresOn": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.authentication_expiry_date"}, "widget-data-options": {"registerId": "6b06a95a-9a6c-5a33-a33d-c1625716c59c", "fieldPrefix": "6b06a95a-9a6c-5a33-a33d-c1625716c59c", "foundationalIdPath": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.fayda_fin", "internalRecordIdPath": "6b06a95a-9a6c-5a33-a33d-c1625716c59c.internal_record_id"}}'::jsonb));
    END IF;
  ELSE result:=node;
  END IF;
  RETURN result;
END $$;
UPDATE g2p_register_sections SET section_ui_schema=pg_temp.oan_popup_identity(section_ui_schema)
WHERE section_ui_schema IS NOT NULL;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM g2p_register_sections WHERE
   jsonb_path_exists(section_ui_schema,'lax $.** ? (@."widget" == "oan-identity-verification")')) THEN
   RAISE EXCEPTION 'Popup-only Fayda widget is missing from domain identity metadata';
 END IF;
 IF EXISTS(SELECT 1 FROM g2p_register_sections, jsonb_path_query(section_ui_schema,'lax $.** ? (@."widget-data-path" == "6b06a95a-9a6c-5a33-a33d-c1625716c59c.foundational_id")') w
   WHERE w->>'widget' <> 'display') THEN
   RAISE EXCEPTION 'FIN must be collected only in the Fayda popup';
 END IF;
END $$;
