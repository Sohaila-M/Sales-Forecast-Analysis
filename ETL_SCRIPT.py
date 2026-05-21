import json
import logging
import pandas as pd
import os
# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("etl")

# ═════════════════════════════════════════════════════════════════════════════
#  EXTRACT
# ═════════════════════════════════════════════════════════════════════════════

def load_json(path: str) -> list:
    """Load a JSON file and always return a list of records."""
    log.info("Loading %s …", path)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict):
        raw = [raw]
    log.info("  → %d records loaded", len(raw))
    return raw


# ═════════════════════════════════════════════════════════════════════════════
# LOAD (CSV output)
# ═════════════════════════════════════════════════════════════════════════════

def export_table(df, table_name):

    output_dir = "" #Write the desired output directory

    # Create folder if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    path = f"{output_dir}/{table_name}.csv"

    df.to_csv(path, index=False)

    log.info(f"Exported: {path}")


def assert_pk(df: pd.DataFrame, col: str, table: str) -> None:
    assert df[col].is_unique, f"Primary key '{col}' is not unique in {table}!"
    assert df[col].notna().all(), f"Primary key '{col}' has nulls in {table}!"

# ═════════════════════════════════════════════════════════════════════════════
# DIM_DATE  – calendar spine (day grain)
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_date(min_date: str, max_date: str) -> pd.DataFrame:
    """
    Full calendar table covering min_date → max_date.
    DateKey (YYYYMMDD integer) is the primary key 
    """
    log.info("Building dim_date (%s → %s) …", min_date, max_date)
    dates = pd.date_range(min_date, max_date, freq="D")
    df = pd.DataFrame({"Date": dates})

    df["DateKey"]      = df["Date"].dt.strftime("%Y%m%d").astype(int)
    df["Year"]         = df["Date"].dt.year.astype(int)          # FK → dim_year
    df["Quarter"]      = df["Date"].dt.quarter.astype(int)
    df["Month"]        = df["Date"].dt.month.astype(int)
    df["MonthName"]    = df["Date"].dt.strftime("%B")
    df["MonthShort"]   = df["Date"].dt.strftime("%b")
    df["Week"]         = df["Date"].dt.isocalendar().week.astype(int)
    df["DayOfWeek"]    = df["Date"].dt.dayofweek + 1             # 1=Mon … 7=Sun
    df["DayName"]      = df["Date"].dt.strftime("%A")
    df["DayShort"]     = df["Date"].dt.strftime("%a")
    df["IsWeekend"]    = df["DayOfWeek"].isin([6, 7]).astype(int)
    df["YearMonth"]    = df["Date"].dt.strftime("%Y-%m")
    df["YearQuarter"]  = "Q" + df["Quarter"].astype(str) + " " + df["Year"].astype(str)
    df["DateLabel"]    = df["Date"].dt.strftime("%d %b %Y")


    cols = ["DateKey", "Date", "Year", "Quarter", "YearQuarter",
            "Month", "MonthName", "MonthShort", "YearMonth",
            "Week", "DayOfWeek", "DayName", "DayShort",
            "IsWeekend", "DateLabel"]
    df = df[cols].reset_index(drop=True)
    assert_pk(df, "DateKey", "dim_date")
    log.info("  dim_date: %d rows", len(df))
    return df
# ═════════════════════════════════════════════════════════════════════════════
# DIM_YEAR  – year bridge (solves the day-vs-year granularity gap)
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_year(all_years: list[int]) -> pd.DataFrame:
    """
    One row per calendar year.
    Sits between dim_date (M) and fact_forecast (M),
    so a single year filter propagates to both fact tables.

    Relationship directions in Power BI:
      dim_year[Year] 1:M dim_date[Year]        — year filters day-level dates
      dim_year[Year] 1:M fact_forecast[Year]   — year filters forecast rows
    """
    log.info("Building dim_year …")
    df = pd.DataFrame({"Year": sorted(set(all_years))})
    df["Year"] = df["Year"].astype(int)
    df["YearLabel"] = df["Year"].astype(str)

    
    assert_pk(df, "Year", "dim_year")
    log.info("  dim_year: %d rows", len(df))
    return df
# ═════════════════════════════════════════════════════════════════════════════
# DIM_PRODUCT
# ═════════════════════════════════════════════════════════════════════════════
def extract_color(product_name):
    KNOWN_COLORS = {
    "Black", "White", "Azure", "Green", "Grey", "Orange",
    "Red", "Blue", "Silver", "Gold" ,"Yellow","Pink","Orange","Brown"}
    words = product_name.split()
    
    # check last word
    last_word = words[-1]

    if last_word in KNOWN_COLORS:
        return last_word
    else:
        return "Unknown"
def build_dim_product(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Builds DIM_PRODUCT table.

    Grain:
        One row per ProductKey (SKU / product variant level)

    Notes:
        - Color is unreliable in source → extracted from Product Name (Not all Product names include color)
        - ProductFamily is derived by removing Color (if found) from Product Name
    """

    log.info("Building dim_product …")

    # ------------------------------------------------------------
    # Step 1: Select needed columns
    # ------------------------------------------------------------
    cols = ["ProductKey", "Product Name", "Brand", "Subcategory", "Category"]

    df = df_raw[cols].copy()

    # ------------------------------------------------------------
    # Step 2: Extract Color from Product Name
    # ------------------------------------------------------------
    df["Color"] = df["Product Name"].apply(extract_color)
    # ------------------------------------------------------------
    # Step 3: Build ProductFamily (remove color if found)
    # ------------------------------------------------------------
    # Example:
    #   "Camera M300 Black" → "Camera M300"
    df["ProductFamily"] = df["Product Name"]
    df.loc[df["Color"] != "Unknown", "ProductFamily"] = (
    df["Product Name"].str.rsplit(" ", n=1).str[0] )

    # ------------------------------------------------------------
    # Step 4: Remove duplicates at ProductKey grain
    # ------------------------------------------------------------
    df = (
        df.drop_duplicates(subset=["ProductKey"])
          .sort_values("ProductKey")
          .reset_index(drop=True)
    )

    # ------------------------------------------------------------
    # Step 5: Validate primary key
    # ------------------------------------------------------------
    assert_pk(df, "ProductKey", "dim_product")

    log.info("  dim_product: %d rows", len(df))

    return df
# ═════════════════════════════════════════════════════════════════════════════
# DIM_CUSTOMER
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_customer(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    CustomerKey is PK.  CountryRegion is FK → dim_geography.
    Profile fields (Name, Education, Occupation) are null for ~90% of rows;
    we resolve the best available profile per CustomerKey.
    Geography columns (City, State, CountryRegion) are always populated.
    """
    log.info("Building dim_customer …")
    all_keys = (
        df_raw[["CustomerKey", "Customer Code", "City", "State", "CountryRegion"]]
        .drop_duplicates(subset=["CustomerKey"])
    )
    with_profile = (
        df_raw[["CustomerKey", "Name", "Education", "Occupation"]]
        .dropna(subset=["Name"])
        .drop_duplicates(subset=["CustomerKey"])
    )
    df = all_keys.merge(with_profile, on="CustomerKey", how="left")

    # Standardise Customer Code
    df["Customer Code"] = df["Customer Code"].str.upper().str.strip()
    

    # Fill missing profile with explicit 'Unknown'
    df[["Name", "Education", "Occupation"]] = (
        df[["Name", "Education", "Occupation"]].fillna("Unknown")
    )

    df = df.sort_values("CustomerKey").reset_index(drop=True)
    assert_pk(df, "CustomerKey", "dim_customer")
    log.info(
        "  dim_customer: %d rows (%d with full profile)",
        len(df),
        with_profile["CustomerKey"].nunique(),
    )
    return df

# ═════════════════════════════════════════════════════════════════════════════
# DIM_BRAND  – brand bridge
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_brand(brands: list[str]) -> pd.DataFrame:
    """
    One row per brand.
    Sits between dim_product (M) and fact_forecast (M), so a brand slicer
    cross-filters both fact tables without a many-to-many ambiguity.

    """
    log.info("Building dim_brand …")
    df = pd.DataFrame({"Brand": sorted(set(brands))})
    df["BrandKey"] = range(1, len(df) + 1)
    df = df[["BrandKey", "Brand"]]
    assert_pk(df, "Brand", "dim_brand")
    log.info("  dim_brand: %d rows", len(df))
    return df
# ═════════════════════════════════════════════════════════════════════════════
# DIM_GEOGRAPHY  – country/region dimension
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_geography(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    One row per country.  CountryRegion is the PK (matches keys in
    dim_customer and fact_forecast directly — no surrogate needed).

    """
    log.info("Building dim_geography …")
    geo = (
        df_raw[["CountryRegion", "Continent"]]
        .dropna()
        .drop_duplicates(subset=["CountryRegion"])
        .sort_values("CountryRegion")
        .reset_index(drop=True)
    )
    assert_pk(geo, "CountryRegion", "dim_geography")
    geo["CountryRegionKey"] = geo.index + 1
    log.info("  dim_geography: %d rows", len(geo))
    return geo

# ═════════════════════════════════════════════════════════════════════════════
# FACT_SALES  – transaction grain (day × product × customer)
# ═════════════════════════════════════════════════════════════════════════════

def build_fact_sales(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Grain: one sales transaction line.
    FK columns: DateKey → dim_date, ProductKey → dim_product,
                CustomerKey → dim_customer.
    Geography is intentionally removed — available via dim_customer → dim_geography.
    Measures: Quantity, NetPrice, TotalSale.
    """
    log.info("Building fact_sales …")
    df = df_raw.copy()

    df["DateKey"] = df["OrderDate"].dt.strftime("%Y%m%d").astype(int)
    df["Year"]    = df["OrderDate"].dt.year.astype(int)

    # Derived measure
    df["TotalSale"] = (df["Net Price"] * df["Quantity"]).round(4)
    df.rename(columns={"Net Price": "NetPrice"}, inplace=True)

    # Keep only FK keys + measures (remove denormalized dim attributes)
    fact_cols = ["DateKey", "Year", "ProductKey", "CustomerKey",
                 "Quantity", "NetPrice", "TotalSale"]
    fact = df[fact_cols].reset_index(drop=True)
    fact.index.name = "SaleID"
    fact = fact.reset_index()
    fact["SaleID"] += 1   # 1-based

    log.info("  fact_sales: %d rows", len(fact))
    return fact


# ═════════════════════════════════════════════════════════════════════════════
# FACT_FORECAST  – annual grain (year × brand × country)
# ═════════════════════════════════════════════════════════════════════════════

def build_fact_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """
    Grain: one forecast value per Year × Brand × CountryRegion.
    FK columns: Year → dim_year, Brand → dim_brand,
                CountryRegion → dim_geography.
    """
    log.info("Building fact_forecast …")
    

    neg = (df["Forecast"] < 0).sum()
    if neg:
        log.warning("  %d negative forecast values", neg)

    df = df.sort_values(["Year", "CountryRegion", "Brand"]).reset_index(drop=True)
    df.index.name = "ForecastID"
    df = df.reset_index()
    df["ForecastID"] += 1

    # Explicit FK ordering for clarity
    df = df[["ForecastID", "Year", "Brand", "CountryRegion", "Forecast"]]
    log.info("  fact_forecast: %d rows", len(df))
    return df

# ═════════════════════════════════════════════════════════════════════════════
# 1- Data Exploration
# ═════════════════════════════════════════════════════════════════════════════
#Write the path of the JSON files
sales_df    = pd.DataFrame(load_json(r"")) 
forecast_df = pd.DataFrame(load_json(r""))
#Clean and validate the Sales table
log.info("── Quality report for Sales:  ──────────────────────────────")
log.info("   Shape : %d rows × %d cols", *sales_df.shape)
log.info ("── Snapshot of Data:  ──────────────────────────────")
log.info(sales_df.head())
log.info("Sales info :  ")
log.info  (sales_df.info())
null_cols = sales_df.isnull().sum()
null_cols = null_cols[null_cols > 0]
if null_cols.empty:
    log.info("  Nulls : none")
else:
    for col, cnt in null_cols.items():
        log.info("  Nulls : %-30s %d  (%.1f%%)", col, cnt, cnt / len(sales_df) * 100)
dupe_rows = sales_df.duplicated().sum()
log.info("  Dupes : %d full-row duplicates", dupe_rows)
log.info("Investigating the huge number of duplications")
log.info(
sales_df.groupby([
    "ProductKey",
    "CustomerKey",
    "OrderDate"
]).size().sort_values(ascending=False) )
log.info("We can see that the duplication is not logical so we must remove duplicates before modelling")
sales_df = sales_df.drop_duplicates()
log.info("After removing duplicates")
dupe_rows = sales_df.duplicated().sum()
log.info("  Dupes : %d full-row duplicates", dupe_rows)
# Data cleaning 
# Strip leading/trailing whitespace from all string columns
str_cols = sales_df.select_dtypes(include="object").columns
sales_df[str_cols] = sales_df[str_cols].apply(lambda s: s.str.strip() if s.dtype == "object" else s)

# Inconsistent Data Types
log.info("Converting OrderDate to Datetime data type")
sales_df["OrderDate"] = pd.to_datetime(sales_df["OrderDate"], format="%m/%d/%Y", errors="coerce")

invalid_dates = sales_df["OrderDate"].isna().sum()
if invalid_dates:
        log.warning("  %d rows have unparseable OrderDate – will be excluded from fact table", invalid_dates)
        sales_df = sales_df[sales_df["OrderDate"].notna()].copy()

# Unmapped Attributes
log.info("In the Snapshot of data, Color attribute values aren't colors but the same values as the Subcategory column")
log.info("Comparing Color to Subcategory")
log.info(sales_df[sales_df["Color"] != sales_df["Subcategory"]])
log.info("Color is 100% identical to Subcategory ")
sales_df.drop(columns=["Color"], inplace=True)
log.info("Dropped Color column")

#Clean and validate the forecast table
log.info("── Quality report for forecast:  ──────────────────────────────")
log.info("   Shape : %d rows × %d cols", *forecast_df.shape)
log.info ("── Snapshot of Data:  ──────────────────────────────")
log.info(forecast_df.head())
log.info("forecast info :  ")
log.info  (forecast_df.info())
null_cols = forecast_df.isnull().sum()
null_cols = null_cols[null_cols > 0]
if null_cols.empty:
    log.info("  Nulls : none")
else:
    for col, cnt in null_cols.items():
        log.info("  Nulls : %-30s %d  (%.1f%%)", col, cnt, cnt / len(forecast_df) * 100)
dupe_rows = forecast_df.duplicated().sum()
log.info("  Dupes : %d full-row duplicates", dupe_rows)
log.info("  forecast.json : clean — no nulls, no duplicates")
# Strip leading/trailing whitespace from all string columns
str_cols = forecast_df.select_dtypes(include="object").columns
forecast_df[str_cols] = forecast_df[str_cols].apply(lambda s: s.str.strip() if s.dtype == "object" else s)
# ═════════════════════════════════════════════════════════════════════════════
# 2- Data Modelling (Star Schema)
# ═════════════════════════════════════════════════════════════════════════════
dim_date      = build_dim_date(sales_df["OrderDate"].min(), sales_df["OrderDate"].max())
all_years     = list(sales_df["OrderDate"].dt.year.dropna().astype(int).unique()) + list(forecast_df["Year"].astype(int).unique())
dim_year      = build_dim_year(all_years)
all_brands    = list(sales_df["Brand"].unique()) + list(forecast_df["Brand"].unique())
dim_brand     = build_dim_brand(all_brands)
dim_product   = build_dim_product(sales_df)
#Replacing Brand with BrandKey to enforce proper dimensional relationships.
dim_product = dim_product.merge(
    dim_brand,
    on="Brand",
    how="left"
)
dim_product = dim_product.drop(columns=["Brand"])
dim_geography = build_dim_geography(sales_df)
dim_customer  = build_dim_customer(sales_df)
#Replacing CountryRegion with CountryRegionKey to enforce proper dimensional relationships.
dim_customer = dim_customer.merge(
    dim_geography,
    on="CountryRegion",
    how="left"
)
dim_customer = dim_customer.drop(columns=["CountryRegion","Continent"])
fact_sales    = build_fact_sales(sales_df)
fact_forecast = build_fact_forecast(forecast_df)
#Replacing CountryRegion with CountryRegionKey to enforce proper dimensional relationships.
fact_forecast = fact_forecast.merge(
    dim_geography,
    on="CountryRegion",
    how="left"
)
fact_forecast = fact_forecast.drop(columns=["CountryRegion","Continent"])
#Replacing Brand with BrandKey to enforce proper dimensional relationships.
fact_forecast = fact_forecast.merge(
    dim_brand,
    on="Brand",
    how="left"
)
fact_forecast = fact_forecast.drop(columns=["Brand"])
fact_forecast =fact_forecast[["ForecastID" , "BrandKey" ,"CountryRegionKey" ,"Year" ,"Forecast"]]

export_table(dim_date, "dim_date")
export_table(dim_year, "dim_year")
export_table(dim_product, "dim_product")
export_table(dim_customer, "dim_customer")
export_table(dim_brand, "dim_brand")
export_table(dim_geography, "dim_geography")
export_table(fact_sales, "fact_sales")
export_table(fact_forecast, "fact_forecast")











