from backend.db.tenancy import Account


def test_account_has_auto_memory_default_false():
    a = Account()
    assert a.auto_memory is False  # model default must be false, not None
    assert "auto_memory" in Account.__table__.columns
