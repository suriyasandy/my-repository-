import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px

from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from arch import arch_model
from scipy.stats import skew, kurtosis, jarque_bera
from statsmodels.tsa.stattools import acf

# --- Constants ---
ROLL_WINDOW = 60
ANNUALIZE = np.sqrt(252)
MANUAL_BANDS = {
    1: (0.00, 0.07, 0.07),
    2: (0.07, 0.50, 0.50),
    3: (0.50, 0.60, 0.60),
    4: (0.60, np.inf, 0.70)
}

st.set_page_config(layout="wide", page_title="FX Thresholding Dashboard")
st.title("FX Thresholding Dashboard – Dynamic, Statistical & ML")

uploaded_fx = st.file_uploader("Upload FX CSV (Date, Currency, LogReturn, VolatilityOHLC)", type="csv")
if not uploaded_fx:
    st.info("Please upload your FX dataset to begin.")
    st.stop()

df = pd.read_csv(uploaded_fx, parse_dates=["Date"])
df = df.sort_values("Date").reset_index(drop=True)

cutoff = df["Date"].max() - pd.Timedelta(days=7)
currencies = df["Currency"].unique().tolist()

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "1. Summary + Trust", "2. Manual vs Statistical", "3. GARCH Forecast",
    "4. ML Anomaly Detection", "5. Regime Detection", "6. Model Comparison",
    "7. Trade Backtesting"
])

with tab1:
    st.subheader("Stakeholder Overview")
    st.markdown("""
    **This platform compares multiple FX thresholding methods**:
    1. **Manual thresholds**: Fixed bands by average annualized OHLC volatility.
    2. **Statistical thresholds**: 60-day rolling vol, annualized via √252, user-selectable percentile.
    3. **GARCH(1,1)**: Forecasts next-day volatility.
    4. **ML Anomalies**: Isolation Forest & One-Class SVM on multivariate features.
    5. **Regime detection**: Z-score shifts in rolling vol.
    """)
    pct = st.slider("Statistical Threshold Percentile", 90, 99, 95)
    df_hist = df[df["Date"] <= cutoff]
    tmp = (df_hist.set_index("Date").groupby("Currency")["LogReturn"]
           .rolling(ROLL_WINDOW).std().reset_index())
    tmp.rename(columns={"LogReturn":"RollVol"}, inplace=True)
    tmp["RollVol"] *= ANNUALIZE
    thresh = (tmp.groupby("Currency")["RollVol"]
              .quantile(pct/100).reset_index(name=f"{pct}thPct_Thresh"))
    latest = (tmp.groupby("Currency")
              .apply(lambda x: x.sort_values("Date")["RollVol"].iloc[-1])
              .reset_index(name="LatestVol"))
    summary = thresh.merge(latest, on="Currency")
    summary["Flag"] = summary["LatestVol"] > summary[f"{pct}thPct_Thresh"]
    st.dataframe(summary.round(4), use_container_width=True)

with tab2:
    st.subheader("Manual vs Statistical Thresholds")
    df_hist = df[df["Date"] <= cutoff]
    avg_ohlc = df_hist.groupby("Currency")["VolatilityOHLC"].mean().reset_index()
    avg_ohlc["AvgAnnVol"] = avg_ohlc["VolatilityOHLC"] * ANNUALIZE
    def find_group_and_thresh(v):
        for g,(lo,hi,t) in MANUAL_BANDS.items():
            if lo<=v<hi: return g,t
        return 4,MANUAL_BANDS[4][2]
    grp_list = avg_ohlc["AvgAnnVol"].apply(find_group_and_thresh).tolist()
    avg_ohlc[["ManualGroup","ManualThreshold"]] = pd.DataFrame(grp_list,
        index=avg_ohlc.index,columns=["ManualGroup","ManualThreshold"])
    tmp = (df_hist.set_index("Date").groupby("Currency")["LogReturn"]
           .rolling(ROLL_WINDOW).std().reset_index())
    tmp.rename(columns={"LogReturn":"RollVol"}, inplace=True)
    tmp["RollVol"] *= ANNUALIZE
    stat_thresh = tmp.groupby("Currency")["RollVol"].quantile(0.95).reset_index(
        name="StatisticalThreshold")
    current_vol = tmp.groupby("Currency").apply(
        lambda x: x.sort_values("Date")["RollVol"].iloc[-1]
    ).reset_index(name="CurrentVol")
    base = avg_ohlc.merge(stat_thresh, on="Currency").merge(current_vol, on="Currency")
    base["Flag_Manual"] = base["CurrentVol"] > base["ManualThreshold"]
    base["Flag_Statistical"] = base["CurrentVol"] > base["StatisticalThreshold"]
    st.dataframe(base.round(4), use_container_width=True)

with tab3:
    st.subheader("GARCH(1,1) Forecast")
    df_hist = df[df["Date"] <= cutoff]
    garch_out = []
    for ccy in currencies:
        series = df_hist[df_hist["Currency"]==ccy]["LogReturn"].dropna()
        if len(series)<100: continue
        try:
            m = arch_model(series, vol="Garch", p=1, q=1)
            r = m.fit(disp="off")
            f = np.sqrt(r.forecast(horizon=1).variance.values[-1][0]) * ANNUALIZE
            cvol = df[df["Currency"]==ccy]["LogReturn"].rolling(
                ROLL_WINDOW).std().dropna().iloc[-1] * ANNUALIZE
            garch_out.append({"Currency":ccy,"GARCHForecast":f,
                              "CurrentVol":cvol,"Flag_GARCH":cvol>f*1.5})
        except:
            pass
    garch_df = pd.DataFrame(garch_out)
    st.dataframe(garch_df.round(4), use_container_width=True)

with tab4:
    st.subheader("ML Anomaly Detection")
    df_hist = df[df["Date"] <= cutoff]
    feat = df_hist.groupby("Currency")["LogReturn"].agg([
        ("Vol", lambda x: x.std()*ANNUALIZE),
        ("Skew", skew),("Kurt", kurtosis)
    ]).dropna()
    if len(feat)>=2:
        if_model = IsolationForest(random_state=42).fit(feat)
        ocsvm = OneClassSVM(nu=0.1).fit(feat)
        feat["Flag_IF"] = if_model.predict(feat)==-1
        feat["Flag_OCSVM"] = ocsvm.predict(feat)==-1
    st.dataframe(feat.round(4), use_container_width=True)

with tab5:
    st.subheader("Regime Shift Detection")
    df["RollVol"] = df.groupby("Currency")["LogReturn"].transform(
        lambda x: x.rolling(ROLL_WINDOW).std()*ANNUALIZE)
    df["ZScore"] = df.groupby("Currency")["RollVol"].transform(
        lambda x: (x-x.mean())/x.std())
    lz = df.groupby("Currency")["ZScore"].last().reset_index()
    lz["Flag_Regime"] = lz["ZScore"].abs()>2
    st.dataframe(lz.round(4), use_container_width=True)

with tab6:
    st.subheader("Model Comparison")
    comp = base[["Currency","Flag_Manual","Flag_Statistical"]].merge(
        garch_df[["Currency","Flag_GARCH"]],on="Currency",how="left"
    ).merge(feat[["Flag_IF","Flag_OCSVM"]],left_on="Currency",
            right_index=True,how="left"
    ).merge(lz[["Currency","Flag_Regime"]],on="Currency",how="left")
    st.dataframe(comp.fillna(False), use_container_width=True)

with tab7:
    st.subheader("Trade Backtesting (% Deviation)")
    trade_file = st.file_uploader("Upload Trade CSV with columns Date,Instrument,DealRate,AllInMarketRate,DeviationPct",type="csv")
    if not trade_file:
        st.info("Upload trade file to backtest.")
        st.stop()
    trades = pd.read_csv(trade_file,parse_dates=["TradeDate"])
    trades["AbsDev"] = trades["DeviationPct"].abs()
    rates = {"INRUSD":83.5,"JPYUSD":145.2,"EURUSD":1.07,"GBPUSD":1.25,
             "INRJPY":0.58,"EURJPY":156.0}
    th = base.copy()
    th["ManualPct"] = th.apply(lambda r: r["ManualThreshold"]/rates.get(r["Currency"],1),axis=1)
    th["StatPct"] = th.apply(lambda r: r["StatisticalThreshold"]/rates.get(r["Currency"],1),axis=1)
    cmp = trades.merge(th[["Currency","ManualPct","StatPct"]],left_on="Instrument",
                       right_on="Currency",how="left")
    cmp["Flag_Manual"] = cmp["AbsDev"]>cmp["ManualPct"]
    cmp["Flag_Stat"] = cmp["AbsDev"]>cmp["StatPct"]
    st.dataframe(cmp.round(5),use_container_width=True)
    summary = cmp[["Flag_Manual","Flag_Stat"]].sum().reset_index()
    summary.columns=["Model","Count"]
    fig=px.bar(summary,x="Model",y="Count",title="Trades Flagged")
    st.plotly_chart(fig,use_container_width=True)
    cmp["TradeKey"] = cmp["Instrument"]+" on "+cmp["TradeDate"].dt.strftime("%Y-%m-%d")
    sel = st.selectbox("Explain trade",cmp["TradeKey"].unique())
    row = cmp[cmp["TradeKey"]==sel].iloc[0]
    with st.expander(f"Details for {sel}"):
        st.markdown(f"""
- Deal Rate: {row['DealRate']}
- Market Rate: {row['AllInMarketRate']}
- % Deviation: {row['AbsDev']:.5f}
- Manual Threshold (%): {row['ManualPct']:.5f}
- Statistical Threshold (%): {row['StatPct']:.5f}
- Manual Flag: {row['Flag_Manual']}
- Statistical Flag: {row['Flag_Stat']}
        """)
