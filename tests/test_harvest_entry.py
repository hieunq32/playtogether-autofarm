import main


def test_harvest_session_starts_from_harvest_icon():
    context = main.BotContext(
        config={},
        detector=None,
        capture=None,
        mouse=None,
    )

    main.begin_harvest_session(context)

    assert context.state == main.BotState.OPEN_HARVEST_ENTRY
