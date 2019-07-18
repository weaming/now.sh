import os
from feedgen.feed import FeedGenerator
from jsonkv import JsonKV

from .common import http_get_url, prepare_dir, url2soup
from .ics_patch import *
from .tiger_api import get_ipo_calendar


def get_ipo_info_html(symbol, enable=False):
    if not enable:
        return "detail disabled"

    path = os.path.join("/tmp/", "db2.json")
    db = JsonKV(path, release_force=True, timeout=3)
    with db:
        key = "ipo_info_html"
        if db[key]:
            if symbol in db[key]:
                return db[key][symbol]
        else:
            db[key] = {}

        soup1 = url2soup(f"https://www.nasdaq.com/symbol/{symbol}")
        if "this is an unknown symbol" in str(soup1).lower():
            return "unknown symbol"

        a_tags = soup1.select(".notTradingIPO a")
        if a_tags:
            url2 = a_tags[0]["href"]
            print(symbol, url2)
            soup2 = url2soup(url2)

            # clean html
            html = ""
            for selector in [
                ".genTable",
                ".ipo-comp-description",
                "#read_more_div_toggle1",
            ]:
                info = soup2.select(selector)
                if not info:
                    continue
                html += str(info[0]).replace("display:none", "")

            # cache
            db[key][symbol] = html

            # debug
            # with open("x.html", "w") as f:
            #     f.write(html)
            # return html
        else:
            return str((soup1.select(".overview-results") or [""])[0])
    return ""


class CalendarBase:
    name = None
    cal_name = None

    def __init__(self, filter_fn=None):
        self.filter_fn = filter_fn

    def gen_ics(self, rss=None):
        """
        :param rss: rss type, atom or rss
        """
        data = self.get_data()
        self.update_ics(data, rss)

    def get_output_root(self):
        return "/tmp/calendar"

    def get_output_path(self, rss=None):
        if rss:
            return self.get_output_path()[:-3] + rss

        output = os.path.expanduser(
            os.path.join(self.get_output_root(), f"{self.name}.ics")
        )
        return output

    def get_exist_events(self):
        ics_output = self.get_output_path()
        prepare_dir(ics_output)

        events = None
        if os.path.isfile(ics_output):
            with open(ics_output) as f:
                container = string_to_container(f.read())
                events = [x for x in container[0] if x.name == "VEVENT"]
        return events

    def get_data(self):
        raise NotImplementedError

    def update_ics(self, data, rss=None):
        c = Calendar(events=self.get_exist_events())
        for e in self.new_events(data):
            # remove by hash
            if e in c.events:
                c.events.remove(e)

            # add the newer one
            c.events.add(e)

        # print(c.events)
        if rss:
            assert rss in ["atom", "rss"]
            fg = FeedGenerator()
            fg.id(self.name)
            fg.title(f"Events of {self.cal_name}")
            for i, e in enumerate(c.events):  # type: Event
                fe = fg.add_entry()
                fe.id(e.uid)
                fe.title(e.name)
                fe.link(href=e.url)
                fe.updated(e.begin.datetime)

                market = e.name.split("|")[0].strip()
                # only latest symbols
                if market == "US" and len(c.events) - i <= 5:
                    # disable for timeout in now server
                    info_html = get_ipo_info_html(e.uid, enable=False)
                    link = f'<p><a href="https://www.nasdaq.com/symbol/{e.uid}">Goto NASDAQ detail page</a></p>'
                    fe.description(f"<p>{e.description}</p> {link} {info_html}")
                else:
                    fe.description(e.description)

            rss_output = self.get_output_path(rss)
            if rss == "atom":
                fg.atom_file(rss_output)
                print(f"wrote {rss_output}")
            elif rss == "rss":
                fg.rss_file(rss_output)
                print(f"wrote {rss_output}")
        else:
            ics_output = self.get_output_path()
            with open(ics_output, "w") as f:
                wrote = False
                for l in c:
                    f.write(l)
                    if not wrote and l.startswith("VERSION"):
                        f.write(f"X-WR-CALNAME:{self.cal_name}\n")
                        wrote = True

            print(f"wrote {ics_output}")

    def new_events(self, data):
        raise NotImplementedError


class CalendarIEX(CalendarBase):
    name = "ipo-iex"
    cal_name = "Upcoming IPOs (IEX)"

    def get_data(self):
        token = os.getenv("IEX_APIS_TOKEN")
        api = f"https://cloud.iexapis.com/stable/stock/market/ipos?token={token}"
        err, data = http_get_url(api, is_json=True)
        if err:
            raise Exception(f"error: {err}; data: {data}")
        return [x for x in data["rawData"]]

    def new_events(self, data):
        for x in data:
            desc = f"date: {x['expectedDate']}, market: {x['market']}, address: {x['address']}, status: {x['status']}, employees: {x['employees']}, revenue: {x['revenue']}"
            begin = f"{x['expectedDate']}T08:00:00-04:00"
            yield Event(
                uid=x["symbol"],
                name=f"{x['symbol']} | {x['companyName'].strip()}",
                begin=begin,
                # duration=datetime.timedelta(hours=12),
                description=desc,
                transparent=True,
                url=x["url"],
                # status={
                #     'FILLED': 'CONFIRMED',
                #     'CANCELLED': 'CANCELLED',
                # }.get(x['status'], 'TENTATIVE'),
                categories={"stock", "financial"},
                alarms=[DisplayAlarm(trigger=get_arrow(begin))],
            )


class CalendarTiger(CalendarBase):
    name = "ipo-tiger"
    cal_name = "IPO (tiger)"

    def get_data(self):
        data = get_ipo_calendar().json()
        try:
            by_date_map = data["data"]
        except KeyError:
            print(data)
            return

        for _, by_region_map in by_date_map.items():
            for _, ipo_list in by_region_map.items():
                for ipo in ipo_list:
                    yield ipo

    def new_events(self, data):
        """
        {
          "currency": "USD",
          "date": "2019-07-17",
          "latestPrice": 11.5,
          "market": "US",
          "name": "斗鱼",
          "priceRange": "11.50 - 14.00",
          "shares": 324623680,
          "symbol": "DOYU"
        }
        """
        for x in data:
            # common keys of ipo list
            date = x["date"]
            market = x["market"]
            name = x["name"]
            symbol = x["symbol"]

            desc = f"date: {date}, market: {market}, name: {name}, date: {date}"
            begin = f"{date}T08:00:00-04:00"
            url = f"https://finance.yahoo.com/quote/{symbol}/"

            if self.filter_fn is not None:
                if not self.filter_fn(x):
                    continue

            yield Event(
                uid=symbol,
                name=f"{market} | {symbol} | {name}",
                begin=begin,
                # duration=datetime.timedelta(hours=12),
                description=desc,
                transparent=True,
                url=url,
                categories={"stock", "financial"},
                alarms=[DisplayAlarm(trigger=get_arrow(begin))],
            )
