"""Compare one day of hourly weather across four cities in Tamil Nadu."""

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import openmeteo_requests
import pandas as pd
import requests_cache
import xarray as xr
from retry_requests import retry

API_URL = "https://archive-api.open-meteo.com/v1/archive"
PROJECT_DIR = Path(__file__).resolve().parent
TIMEZONE = "Asia/Kolkata"
DATE = "2025-10-07"
CITIES = {
    "Chennai": (13.08, 80.27),
    "Coimbatore": (11.02, 76.96),
    "Madurai": (9.93, 78.12),
    "Tirunelveli": (8.71, 77.76),
}
# The order here also determines the API response's variable indexes.
VARIABLES = {
    "shortwave_radiation": "W/m²",
    "temperature_2m": "°C",
    "cloud_cover": "%",
}


def fetch_weather():
    """Fetch all cities in one request, caching responses for one hour."""
    with requests_cache.CachedSession(
        str(PROJECT_DIR / ".cache"), expire_after=3600
    ) as cache_session:
        retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
        client = openmeteo_requests.Client(session=retry_session)
        return client.weather_api(API_URL, params={
            "latitude": [lat for lat, lon in CITIES.values()],
            "longitude": [lon for lat, lon in CITIES.values()],
            "start_date": DATE,
            "end_date": DATE,
            "hourly": list(VARIABLES),
            "timezone": TIMEZONE,
        })


def create_dataset(responses):
    """Combine API arrays into variables with (time, city) dimensions."""
    if len(responses) != len(CITIES):
        raise ValueError("Expected one API response for each city")

    datasets = []
    for city, response in zip(CITIES, responses):
        hourly = response.Hourly()
        # SDK timestamps are Unix seconds. Convert to Indian local time,
        # then remove timezone information for Xarray's datetime64 coordinate.
        timestamps = pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left",
        ).tz_convert(TIMEZONE).tz_localize(None)
        data_vars = {}
        for index, (name, unit) in enumerate(VARIABLES.items()):
            values = hourly.Variables(index).ValuesAsNumpy()
            if len(values) != len(timestamps) or not np.isfinite(values).all():
                raise ValueError(f"Incomplete {name} data for {city}")
            data_vars[name] = ("time", values, {"units": unit})
        datasets.append(xr.Dataset(data_vars, coords={"time": timestamps}))

    dataset = xr.concat(
        datasets,
        dim=pd.Index(list(CITIES), name="city"),
        join="exact",  # All cities must share the same hourly timestamps.
    ).transpose("time", "city")
    dataset.time.attrs["timezone"] = TIMEZONE
    dataset.attrs["source"] = "Open-Meteo Historical Weather API"
    return dataset


def summarize_irradiance(dataset):
    """Return each city's mean (including night) and peak hourly irradiance."""
    radiation = dataset["shortwave_radiation"]
    return pd.DataFrame({
        "Average (W/m²)": radiation.mean("time").to_series(),
        "Peak (W/m²)": radiation.max("time").to_series(),
    })


def plot_irradiance(dataset):
    """Save the hourly comparison and summary together in one image."""
    fig, (ax, summary_ax) = plt.subplots(
        2, 1, figsize=(10, 8), gridspec_kw={"height_ratios": [3, 1.2]},
        layout="constrained",
    )
    for city in CITIES:
        ax.plot(dataset.time.values,
                dataset.shortwave_radiation.sel(city=city).values,
                label=city)
    ax.set(title=f"Hourly solar irradiance in Tamil Nadu — {DATE}",
           xlabel="Local time (Asia/Kolkata)", ylabel="Solar irradiance (W/m²)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    ax.legend()
    summary = summarize_irradiance(dataset)
    summary_ax.axis("off")
    summary_ax.set_title("Solar irradiance summary (average includes nighttime)",
                         fontsize=11)
    table = summary_ax.table(
        cellText=[[city, f"{row.iloc[0]:.2f}", f"{row.iloc[1]:.2f}"]
                  for city, row in summary.iterrows()],
        colLabels=["City", *summary.columns],
        cellLoc="center",
        bbox=[0.1, 0, 0.8, 0.9],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if row == 0:
            cell.set_facecolor("#eef2f6")
            cell.set_text_props(weight="bold")
    path = PROJECT_DIR / "solar_irradiance.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    dataset = create_dataset(fetch_weather())
    for city in CITIES:
        table = dataset.sel(city=city, drop=True).to_dataframe().rename(columns={
            "shortwave_radiation": "Radiation (W/m²)",
            "temperature_2m": "Temperature (°C)",
            "cloud_cover": "Cloud cover (%)",
        })
        print(f"\nHourly weather: {city} | {DATE} | {TIMEZONE}")
        print(table.to_string(float_format="%.2f"))
    print("\nSolar irradiance summary (average includes nighttime)")
    print(summarize_irradiance(dataset).to_string(float_format="%.2f"))
    print(f"\nSaved chart: {plot_irradiance(dataset)}")


if __name__ == "__main__":
    main()
