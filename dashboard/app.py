"""Дашборд продаж (US-20). Читает только витрину analytics — не рабочие таблицы магазина.

Запуск: docker compose --profile dashboard up dashboard → http://localhost:8501
"""

import os
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:5432/shop")
SERIES = "#2a78d6"  # один ряд — один цвет, без легенды: название ряда в заголовке
RU_THOUSANDS = "replace(datum.label, /,/g, ' ')"  # 600,000 -> 600 000

st.set_page_config(page_title="ShopCore — продажи", layout="wide")


@st.cache_resource
def engine():
    return create_engine(DATABASE_URL)


@st.cache_data(ttl=10)
def load(date_from: date, date_to: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = {"d1": date_from, "d2": date_to}
    with engine().connect() as conn:
        sales = pd.read_sql(
            text(
                "SELECT day, product_id, product_name, orders_count, units, revenue_kopecks "
                "FROM analytics.sales_daily WHERE day BETWEEN :d1 AND :d2"
            ),
            conn,
            params=params,
        )
        funnel = pd.read_sql(
            text("SELECT * FROM analytics.orders_daily WHERE day BETWEEN :d1 AND :d2 ORDER BY day"),
            conn,
            params=params,
        )
    return sales, funnel


def rub(kopecks: float) -> str:
    return f"{kopecks / 100:,.0f} ₽".replace(",", " ")


st.title("Продажи")
st.caption("Источник — витрина analytics, которую строит консьюмер Kafka. Обновление раз в 10 секунд.")

today = date.today()
date_from, date_to = st.date_input("Период", value=(today - timedelta(days=13), today), format="DD.MM.YYYY")
sales, funnel = load(date_from, date_to)

if funnel.empty and sales.empty:
    st.info("За период нет данных. Оформите и оплатите пару заказов — события долетят сюда через Kafka.")
    st.stop()

created = int(funnel["created"].sum())
paid = int(funnel["paid"].sum())
expired = int(funnel["expired"].sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Выручка", rub(sales["revenue_kopecks"].sum()))
c2.metric("Оплачено заказов", paid)
c3.metric("Доля оплаченных", f"{paid / created:.0%}" if created else "—", help="Оплачено / оформлено за период")
c4.metric("Доля истёкших", f"{expired / created:.0%}" if created else "—", help="Не оплачены за 15 минут")

left, right = st.columns(2)

with left:
    st.subheader("Выручка по дням")
    by_day = sales.groupby("day", as_index=False)["revenue_kopecks"].sum()
    by_day["Выручка, ₽"] = by_day["revenue_kopecks"] / 100
    by_day["day"] = pd.to_datetime(by_day["day"])
    line = (
        alt.Chart(by_day)
        .mark_line(color=SERIES, strokeWidth=2, point=alt.OverlayMarkDef(color=SERIES, size=60))
        .encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(format="%d.%m", grid=False)),
            y=alt.Y("Выручка, ₽:Q", title=None, axis=alt.Axis(format=",.0f", labelExpr=RU_THOUSANDS, gridOpacity=0.3)),
            tooltip=[alt.Tooltip("day:T", title="День", format="%d.%m.%Y"), alt.Tooltip("Выручка, ₽:Q", format=",.0f")],
        )
        .properties(height=280)
    )
    st.altair_chart(line, use_container_width=True)

with right:
    st.subheader("Топ-5 товаров по выручке")
    top = (
        sales.groupby("product_name", as_index=False)["revenue_kopecks"]
        .sum()
        .nlargest(5, "revenue_kopecks")
        .assign(**{"Выручка, ₽": lambda d: d["revenue_kopecks"] / 100})
    )
    bars = (
        alt.Chart(top)
        .mark_bar(color=SERIES, cornerRadiusEnd=4, height=18)
        .encode(
            x=alt.X("Выручка, ₽:Q", title=None, axis=alt.Axis(format=",.0f", labelExpr=RU_THOUSANDS, gridOpacity=0.3)),
            y=alt.Y("product_name:N", title=None, sort="-x", axis=alt.Axis(labelLimit=240)),
            tooltip=[alt.Tooltip("product_name:N", title="Товар"), alt.Tooltip("Выручка, ₽:Q", format=",.0f")],
        )
        .properties(height=280)
    )
    st.altair_chart(bars, use_container_width=True)

with st.expander("Таблица: воронка заказов по дням"):
    st.dataframe(
        funnel.rename(
            columns={
                "day": "День",
                "created": "Оформлено",
                "paid": "Оплачено",
                "expired": "Истекло",
                "cancelled": "Отменено",
                "refunded": "Возвращено",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
