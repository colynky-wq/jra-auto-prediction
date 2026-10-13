"""Retry PDF gaps through observed JRA public historical calendar routes."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup
import requests

from race_lap_runtime import parse_official_laps

HOST = "https://www.jra.go.jp"
ACCESS = HOST + "/JRADB/accessS.html"


def decode(response) -> str:
    return response.content.decode("cp932", errors="strict")


def month_token(html: str, date8: str) -> str:
    key = date8[2:6]
    match = re.search(r'objParam\["'+re.escape(key)+r'"\]\s*=\s*"([0-9A-F]{2})"', html)
    if not match or "pw01skl10" not in html:
        raise ValueError("historical month route missing from official calendar")
    # Exact public selector code: pw01skl10 + YYYYMM + / + observed objParam.
    return "pw01skl10" + date8[:6] + "/" + match[1]


def result_urls(html: str) -> dict[str, str]:
    out = {}
    for token in re.findall(r"pw01sde(?:01|10)\d{20}/[0-9A-Fa-f]{2}", html):
        match = re.fullmatch(r"pw01sde(?:01|10)(\d{2})(\d{4})(\d{6})(\d{8})/[0-9A-Fa-f]{2}", token)
        out[match[2]+match[1]+match[3]+":"+match[4]] = ACCESS+"?"+urlencode({"CNAME": token})
    return out


class HistoricalResults:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0 KeibaAI-research/1.0"
        response = self.session.get(HOST+"/", timeout=30)
        response.raise_for_status()
        html = decode(response)
        token = re.search(r"pw01skl00999999/[0-9A-Fa-f]{2}", html)
        form = BeautifulSoup(html, "html.parser").find("form", id="commForm01")
        field = form.find("input", id="cname") if form else None
        if token is None or not field or not field.get("name"):
            raise ValueError("official historical calendar entry/form missing")
        self.field = field["name"]
        self.calendar = self.post(token[0])
        self.months, self.routes = {}, {}

    def post(self, token: str) -> str:
        response = self.session.post(ACCESS, data={self.field: token}, headers={"Referer": HOST+"/"}, timeout=30)
        response.raise_for_status()
        return decode(response)

    def fetch(self, expected: dict) -> dict:
        date8, race_id = expected["date8"], expected["raceID"]
        month = date8[:6]
        if month not in self.months:
            self.months[month] = self.post(month_token(self.calendar, date8))
        key = race_id+":"+date8
        prefix = race_id[:10]
        if prefix not in self.routes:
            html = self.months[month]
            candidates = re.findall(r"pw01srl(?:01|10)\d{18}/[0-9A-Fa-f]{2}", html)
            target = race_id[4:6]+race_id[:4]+race_id[6:10]+date8
            token = next((t for t in candidates if re.fullmatch(r"pw01srl(?:01|10)"+target+r"/[0-9A-Fa-f]{2}", t)), None)
            if token is None:
                raise ValueError("official calendar has no matching historical venue/day")
            self.routes[prefix] = result_urls(self.post(token))
        url = self.routes[prefix].get(key)
        if url is None:
            raise ValueError("historical result route missing")
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        record = parse_official_laps(decode(response), race_id, date8, url)
        if record["distanceM"] != expected["distanceM"] or record["surface"] != expected["surface"]:
            raise ValueError("historical HTML course disagrees with history")
        if abs(record["winnerTimeSeconds"]-expected["winnerTimeSeconds"]) > .050000001:
            raise ValueError("historical HTML winner time disagrees with history")
        record["sourceSHA256"] = hashlib.sha256(response.content).hexdigest()
        return record
