"""Paper-book persistence: positions/cash/GTTs survive a process restart
(Phase 11 autonomy piece)."""
from agent.broker.paper_broker import PaperBroker, BUY, MARKET, CNC, SLM
from agent.state import save_paper_book, load_paper_book, save_state, load_state
from agent.loop import LoopState


def test_paper_book_round_trip(tmp_path):
    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0)
    # open a position + park a protective stop GTT
    broker.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type=BUY,
                       quantity=50, product=CNC, order_type=MARKET, last_price=200.0)
    broker.place_gtt_order(tradingsymbol="RELIANCE", exchange="NSE", trigger_values=[180.0])
    cash_before = broker.cash
    pos_before = broker.get_positions()
    assert pos_before and pos_before[0]["quantity"] == 50

    path = tmp_path / ".paper_book.json"
    save_paper_book(path, broker)

    # fresh broker (e.g. next morning's process) restores the book
    fresh = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0)
    assert load_paper_book(path, fresh) is True
    assert fresh.cash == cash_before
    fpos = fresh.get_positions()
    assert len(fpos) == 1
    assert fpos[0]["tradingsymbol"] == "RELIANCE" and fpos[0]["quantity"] == 50
    assert fpos[0]["average_price"] == pos_before[0]["average_price"]
    assert len(fresh.get_gtts()) == 1
    assert len(fresh.get_trades()) == 1


def test_id_sequences_continue_after_restore(tmp_path):
    broker = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0)
    oid1 = broker.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type=BUY,
                              quantity=10, product=CNC, order_type=MARKET, last_price=100.0)
    path = tmp_path / ".paper_book.json"
    save_paper_book(path, broker)

    fresh = PaperBroker(starting_cash=100000.0, price_fn=lambda s: 100.0)
    load_paper_book(path, fresh)
    oid2 = fresh.place_order(exchange="NSE", tradingsymbol="TCS", transaction_type=BUY,
                             quantity=10, product=CNC, order_type=MARKET, last_price=100.0)
    assert oid2 != oid1                      # no id collision across the restart
    assert oid2 == "PAPER000002"


def test_load_missing_file_is_fresh_start(tmp_path):
    broker = PaperBroker(starting_cash=100000.0)
    assert load_paper_book(tmp_path / "nope.json", broker) is False
    assert broker.get_positions() == []


def test_corrupt_file_is_fail_safe(tmp_path):
    path = tmp_path / ".paper_book.json"
    path.write_text("{ not valid json", encoding="utf-8")
    broker = PaperBroker(starting_cash=100000.0)
    assert load_paper_book(path, broker) is False  # treated as fresh start, no crash


def test_loop_state_still_round_trips(tmp_path):
    # the existing LoopState persistence is untouched by the paper-book addition
    path = tmp_path / ".loop_state.json"
    save_state(path, LoopState(day_open_equity=95000.0, high_water_mark=101000.0,
                               current_date="2026-05-29"))
    st = load_state(path)
    assert st.day_open_equity == 95000.0 and st.high_water_mark == 101000.0
    assert st.current_date == "2026-05-29"
