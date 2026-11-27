from mp_profile import load_profile


def test_v1_1_7_profile_matches_native_inventory():
    profile = load_profile()

    assert len(profile.text_tokens) == 28
    assert len(profile.data["capabilities"]["mp_functions"]) == 84
    assert len(profile.data["capabilities"]["vrl_functions"]) == 196
    assert "JSON" not in profile.text_tokens
    assert "DATETIME_SYSLOG_NOTZ" not in profile.text_tokens
    assert "mp_json" not in profile.functions
    assert "mp_submessage" not in profile.functions
    assert "mp_table_search" not in profile.functions
    assert "parse_timestamp" in profile.functions
    syntax = profile.data["capabilities"]["text_syntax"]
    assert syntax["temporary_capture"] == "{$user=STRING}"
    assert syntax["until"] == '{$user=UNTIL("]")}'
    assert "$user:WORD" in syntax["not_supported"]


def test_profile_separates_semantic_names_storage_types_and_managed_fields():
    profile = load_profile()

    assert profile.target_name("event_src.title") == "event_src_title"
    assert profile.storage_fields["src_ip"] == "Nullable(String)"
    assert profile.storage_fields["src_port"] == "Nullable(Int64)"
    assert profile.storage_fields["start_time"] == "Nullable(DateTime('UTC'))"
    assert profile.storage_fields["src_asset"] == "Nullable(UUID)"
    assert profile.data["taxonomy"]["event_field_aliases"] == {"time": "timestamp"}

    assert profile.validate_target("src.ip") is None
    assert profile.validate_target("time") is None
    assert profile.validate_target("uuid") == "saturn_managed_field_forbidden:uuid"
    assert profile.validate_target("invented.field") == "unknown_logicore_field:invented.field"
