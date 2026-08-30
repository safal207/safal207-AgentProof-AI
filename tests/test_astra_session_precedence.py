from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {
        finding.code for finding in verify_causal_economic_outcome(events)
    }


def contract(*, session_id, dimensions):
    return event(
        Stage.POLICY_DECISION,
        "requires_payment_session_principal_binding",
        {"required": True, "dimensions": dimensions},
        "session-contract",
        session_id=session_id,
    )


def binding():
    return event(
        Stage.POLICY_DECISION,
        "payment_session_binding",
        {
            "user_id": "user-a",
            "agent_id": "research-agent",
            "payment_instrument_id": "instrument-a",
            "merchant_origin": "https://merchant.example",
            "operation_id": "operation-a",
        },
        "session-registry",
        authoritative=True,
        session_id="session-a",
        operation_id="operation-a",
    )


def use(*, agent_id="research-agent", instrument_id="instrument-a"):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "payment_session_use",
        {
            "user_id": "user-a",
            "agent_id": agent_id,
            "payment_instrument_id": instrument_id,
            "merchant_origin": "https://merchant.example/paid",
            "operation_id": "operation-a",
        },
        "attempt-context",
        session_id="session-a",
        operation_id="operation-a",
        attempt_id="attempt-a",
    )


def test_session_contract_cannot_remove_global_instrument_requirement():
    found = codes(
        [
            contract(
                session_id=None,
                dimensions=[
                    "agent_id",
                    "payment_instrument_id",
                    "merchant_origin",
                    "operation_id",
                ],
            ),
            contract(session_id="session-a", dimensions=["user_id"]),
            binding(),
            use(instrument_id="instrument-b"),
        ]
    )

    assert found == {"SESSION_INSTRUMENT_CROSSOVER"}


def test_session_contract_can_add_to_global_requirements():
    found = codes(
        [
            contract(session_id=None, dimensions=["user_id"]),
            contract(
                session_id="session-a",
                dimensions=["payment_instrument_id"],
            ),
            binding(),
            use(
                agent_id="undeclared-agent-change",
                instrument_id="instrument-b",
            ),
        ]
    )

    assert found == {"SESSION_INSTRUMENT_CROSSOVER"}


def test_conflicting_global_contract_prevents_narrow_session_evaluation():
    found = codes(
        [
            contract(session_id=None, dimensions=["agent_id"]),
            contract(session_id=None, dimensions=["user_id"]),
            contract(session_id="session-a", dimensions=["user_id"]),
            binding(),
            use(),
        ]
    )

    assert found == {"PAYMENT_SESSION_CONTRACT_CONFLICT"}
