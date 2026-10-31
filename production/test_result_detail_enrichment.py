import pandas as pd

from result_detail_enrichment import enrich_result_dataframe


def test_extracts_passing_order_and_last3f_without_changing_result_core():
    html = """
    <html><body><table>
      <thead><tr><th>着順</th><th>馬番</th><th>馬名</th><th>通過</th><th>上り</th></tr></thead>
      <tbody>
        <tr><td>1</td><td>5</td><td>差し馬</td><td>8-8-6-3</td><td>33.4</td></tr>
        <tr><td>2</td><td>1</td><td>先行馬</td><td>1-1-1-1</td><td>34.8</td></tr>
      </tbody>
    </table></body></html>
    """
    parsed = pd.DataFrame(
        [
            {"raceID": "202605050111", "number": 1, "finish": 2, "resultHorseName": "先行馬"},
            {"raceID": "202605050111", "number": 5, "finish": 1, "resultHorseName": "差し馬"},
        ]
    )
    out = enrich_result_dataframe(html, "202605050111", parsed)
    by_no = {int(r.number): r for r in out.itertuples()}
    assert by_no[5].passingOrder == "8-8-6-3"
    assert by_no[5].last3F == 33.4
    assert by_no[1].passingOrder == "1-1-1-1"
    assert by_no[1].last3F == 34.8
    assert by_no[5].finish == 1
