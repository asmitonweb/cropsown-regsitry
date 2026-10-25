-- An intake identifies its farmer by the Farmer ID and one Fayda ID (FIN or FAN,
-- or Verify with Fayda); everything else about the farmer comes from the Farmer
-- Registry. The Farmer Identity section holds only those, with the verification
-- and the reference status beside them. Crop year and season move to their own
-- Production Season section, bound right after Farmer Identity on every intake
-- that uses it. The farmer's name is not asked for at intake.
-- Runs last, on the sections as the earlier files leave them; rerunning it is a no-op.
CREATE OR REPLACE FUNCTION pg_temp.oan_widgets(ui jsonb, ids text[])
RETURNS jsonb LANGUAGE sql AS $$
  SELECT COALESCE(jsonb_agg(w ORDER BY array_position(ids, w->>'widget-id')), '[]'::jsonb)
  FROM jsonb_path_query(ui, 'lax $.**') w
  WHERE jsonb_typeof(w) = 'object' AND w ? 'widget' AND w->>'widget-id' = ANY(ids)
$$;

DO $$
DECLARE
  s record;
  ui jsonb;
  season jsonb;
BEGIN
  FOR s IN SELECT * FROM (VALUES
      ('cropsown_intake_record_section_01', 'cropsown_intake_season_section_01', 'cs_intake_season'),
      ('cropsown_common_intake_record_section_01', 'cropsown_common_intake_season_section_01', 'cs_common_intake_season')
    ) AS v(src, dst, mnemonic)
  LOOP
    SELECT section_ui_schema INTO ui FROM g2p_register_sections WHERE section_id = s.src;
    CONTINUE WHEN ui IS NULL;

    season := pg_temp.oan_widgets(ui, ARRAY['crop_year', 'production_season']);
    IF jsonb_array_length(season) > 0 THEN
      INSERT INTO g2p_register_sections (register_id, section_id, section_register_id, is_core_section,
          section_mnemonic, section_description, documents_required, no_of_verifications_required, is_list,
          section_weightage, cr_auto_approve_for_bene_portal, cr_auto_approve_for_agent_portal,
          cr_auto_approve_for_staff_portal, cr_auto_approve_for_partner, section_ui_schema)
      SELECT register_id, s.dst, section_register_id, is_core_section,
          s.mnemonic, 'Production Season', documents_required, no_of_verifications_required, is_list,
          section_weightage, cr_auto_approve_for_bene_portal, cr_auto_approve_for_agent_portal,
          cr_auto_approve_for_staff_portal, cr_auto_approve_for_partner,
          jsonb_build_object('section-id', s.dst, 'section-title', 'Production Season',
            'section-editable', true, 'section-required-for-submit', true,
            'panels', jsonb_build_array(jsonb_build_object(
              'panel-id', 'vertical_panel_' || s.mnemonic, 'panel-orientation', 'vertical', 'widgets', season)))
      FROM g2p_register_sections WHERE section_id = s.src
      ON CONFLICT (section_id) DO UPDATE SET
        section_mnemonic = EXCLUDED.section_mnemonic,
        section_description = EXCLUDED.section_description,
        section_ui_schema = EXCLUDED.section_ui_schema;
    END IF;

    UPDATE g2p_register_sections
    SET section_ui_schema = (ui - 'panels') || jsonb_build_object('panels', jsonb_build_array(jsonb_build_object(
          'panel-id', 'horizontal_panel_' || section_mnemonic, 'panel-orientation', 'horizontal',
          'panels', jsonb_build_array(
            jsonb_build_object('panel-id', 'vertical_panel_' || section_mnemonic || '_ids',
              'panel-orientation', 'vertical',
              'widgets', pg_temp.oan_widgets(ui, ARRAY['farmer_id', 'fayda_fin', 'fayda_fan_id'])),
            jsonb_build_object('panel-id', 'vertical_panel_' || section_mnemonic || '_verification',
              'panel-orientation', 'vertical',
              'widgets', pg_temp.oan_widgets(ui, ARRAY['identity_verification', 'farmer_reference_status']))))))
    WHERE section_id = s.src;

    -- Bind Production Season right after Farmer Identity wherever Farmer Identity
    -- is bound, then renumber the tab (the base bindings may have been reseeded).
    INSERT INTO g2p_intake_form_ui_tab_sections (tab_section_id, tab_id, section_id, section_order)
    SELECT md5(ident.tab_id || ':' || s.dst)::uuid::text, ident.tab_id, s.dst, ident.section_order
    FROM g2p_intake_form_ui_tab_sections ident
    WHERE ident.section_id = s.src
    ON CONFLICT (tab_section_id) DO NOTHING;
    UPDATE g2p_intake_form_ui_tab_sections ts SET section_order = ordered.position
    FROM (
      SELECT x.tab_section_id, row_number() OVER (PARTITION BY x.tab_id ORDER BY
          CASE WHEN x.section_id = s.dst THEN ident.section_order + 0.5 ELSE x.section_order END,
          x.section_id) AS position
      FROM g2p_intake_form_ui_tab_sections x
      JOIN g2p_intake_form_ui_tab_sections ident ON ident.tab_id = x.tab_id AND ident.section_id = s.src
    ) ordered
    WHERE ts.tab_section_id = ordered.tab_section_id AND ts.section_order <> ordered.position;
  END LOOP;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM g2p_register_sections s,
      jsonb_path_query(s.section_ui_schema, 'lax $.** ? (exists (@."widget"))') w
    WHERE s.section_id IN ('cropsown_intake_record_section_01', 'cropsown_common_intake_record_section_01')
      AND w->>'widget-id' NOT IN ('farmer_id', 'fayda_fin', 'fayda_fan_id', 'identity_verification',
                                  'farmer_reference_status')) THEN
    RAISE EXCEPTION 'Farmer Identity on an intake may hold only the Farmer ID, FIN, FAN and Fayda verification';
  END IF;
  IF EXISTS (SELECT 1 FROM g2p_intake_form_ui_tab_sections ident
    WHERE ident.section_id IN ('cropsown_intake_record_section_01', 'cropsown_common_intake_record_section_01')
      AND NOT EXISTS (SELECT 1 FROM g2p_intake_form_ui_tab_sections x
        WHERE x.tab_id = ident.tab_id AND x.section_order = ident.section_order + 1
          AND x.section_id IN ('cropsown_intake_season_section_01', 'cropsown_common_intake_season_section_01'))) THEN
    RAISE EXCEPTION 'Every intake with Farmer Identity must ask for the Production Season right after it';
  END IF;
END $$;
