from miniclaw.llm.base import merge_stream_fragment


def test_merge_stream_fragment_preserves_repeated_digits():
    text = ""
    for fragment in ["{\"run_steps\":400", "0", "0", "}"]:
        text = merge_stream_fragment(text, fragment)

    assert text == "{\"run_steps\":40000}"


def test_merge_stream_fragment_preserves_repeated_letters():
    text = ""
    for fragment in ['{"stress_component":"px', 'x', '"}']:
        text = merge_stream_fragment(text, fragment)

    assert text == '{"stress_component":"pxx"}'


def test_merge_stream_fragment_preserves_adjacent_json_braces():
    text = ""
    for fragment in ['{"updates":{"tension.run_steps":40000}', "}"]:
        text = merge_stream_fragment(text, fragment)

    assert text == '{"updates":{"tension.run_steps":40000}}'


def test_merge_stream_fragment_accepts_cumulative_snapshots():
    text = merge_stream_fragment("", '{"path"')
    text = merge_stream_fragment(text, '{"path":"demo.json"}')

    assert text == '{"path":"demo.json"}'
