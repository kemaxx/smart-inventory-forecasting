import time
import pandas as pd
import gspread
import numpy as np
import re
from datetime import timedelta
import json
import math
from dotenv import load_dotenv
from prophet import Prophet

load_dotenv()
import os
import warnings

warnings.filterwarnings("ignore")


class MarketList():
    def __init__(self):
        STEAM_TALENT_SERVICE_ACCOUNT = os.environ.get("STEAM_TALENT_ACCOUNT")
        self.gc = gspread.service_account(STEAM_TALENT_SERVICE_ACCOUNT)
        self.initialize_sheets_page()

    def get_extras_and_exceptions_stock_name(self):
        """
        This method gets exempted and new stock into the issues voucher for computation
        :return:
        """
        time.sleep(1)

        gc = self.gc
        extras_sheet = gc.open_by_key("19ePbzsPDeY38_gkSs4FT7nxUQWQczaJ1F5UNfGOOk1g")
        extras_worksheet = extras_sheet.worksheet("Extras")
        extras_values_list = extras_worksheet.get_all_values()
        extras_values_list = [[str(x).replace('"', '') for x in record] for record in extras_values_list]
        extras_df = pd.DataFrame(extras_values_list[1:], columns=extras_values_list[0])
        extras_df["Amount"] = extras_df["Amount"].str.replace([',', ''], '').astype(float)
        extras_items_list = extras_df["Stock Name"].unique().tolist()

        return extras_items_list

    def get_available_categories(self):
        """
        Get all available categories from issues voucher
        """
        issue_df = self.get_issue_voucher()
        # Get unique categories and remove any empty/null values
        categories = issue_df["Category"].dropna().unique().tolist()
        # Remove empty strings and clean up the data
        categories = [cat.strip() for cat in categories if cat and str(cat).strip()]
        # Remove duplicates and sort
        categories = sorted(list(set(categories)))
        return categories

    def get_items_by_categories(self, selected_categories, x_items=150, excluded_items=None):
        """
        Get top items filtered by selected categories and excluding specific items
        """
        if excluded_items is None:
            excluded_items = []
            
        issue_df = self.get_issue_voucher()
        
        # Filter by selected categories
        filtered_df = issue_df[issue_df["Category"].isin(selected_categories)]
        
        # Get top items from selected categories
        top_items = filtered_df["Item name"].value_counts().head(x_items)
        items_list = top_items.index.tolist()
        
        # Remove excluded items
        items_list = [item for item in items_list if item not in excluded_items]
        
        return items_list

    def get_top_x_number_of_items_to_buy(self, x_items=150):
        issue_df = self.get_issue_voucher()
        categories = ['WINE', 'BEVERAGE', 'FOOD ITEM', 'ELECTRONICS AND LIGHTING', 'CLEANING SUPPLY',
                      'GUEST SUPPLY', 'DRINKS', 'CONSUMABLE', 'PRINTING AND STATIONERIES', 'VEGETABLE', 'BITE']
        sel_cat = ['BEVERAGE', 'FOOD ITEM', 'CLEANING SUPPLY', 'GUEST SUPPLY', 'CONSUMABLE',
                   'PRINTING AND STATIONERIES']

        sel_item_df = issue_df.loc[issue_df["Category"].isin(sel_cat)]
        top_x_items = sel_item_df["Item name"].value_counts()[:x_items]
        items_db = top_x_items.index.tolist()
        return items_db

    def remove_outliers_col_freq(self, df):
        """"
        The remove_outliers_col_freq function calculates the number of days between consecutive dates in a DataFrame,
        identifies and removes outliers using the IQR method, calculates the average time difference,
        and adds an additional day if needed.
        """

        df["diff"] = df["Date"] - df["Date"].shift(1)

        statistics = df["diff"].describe()

        Q1 = statistics["25%"]
        Q3 = statistics["75%"]

        IQR = statistics["75%"] - statistics["25%"]

        lower_bound, upper_bound = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR

        crt1 = df["diff"] <= lower_bound
        crt2 = df["diff"] >= upper_bound

        avg_date_time = df.where(~(crt1 | crt2))['diff'].mean()

        f = f"Timedelta('{avg_date_time}')"

        # Your timedelta string
        timedelta_str = str(f)

        # Use regular expression to extract days, hours, minutes, and seconds
        match = re.match(r"Timedelta\('(\d+) days (\d+):(\d+):(\d+)(.(\d+))*'\)", timedelta_str)

        if match:
            days, hours, minutes, seconds = int(match.groups()[0]), int(match.groups()[1]), int(match.groups()[2]), int(
                match.groups()[3])
            time_part_timedelta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

            if hours >= 13:
                days += 1
                return days
            elif days == 0:
                days = days + 1
                return days
            else:
                return days
        else:
            return np.nan

    def forecast_stock_usage_with_prophet(self, item, forecast_period='monthly', safety_cushion=1.10):
        """
        Enhanced method that can forecast weekly, monthly, or custom periods using Facebook's Prophet model
        
        Parameters:
        - item: stock item name
        - forecast_period: 'weekly', 'monthly', or number of days
        - safety_cushion: safety multiplier for forecast
        """
        issues_df = self.get_issue_voucher()
        item_df = issues_df.loc[issues_df["Item name"] == item, :].dropna()

        if item_df.empty:
            return 0.0

        # Determine aggregation frequency and forecast periods
        if forecast_period == 'weekly':
            freq = 'W'
            periods = 1
        elif forecast_period == 'monthly':
            freq = 'M' 
            periods = 1
        elif isinstance(forecast_period, int):
            # Custom number of days
            freq = 'D'
            periods = forecast_period
        else:
            freq = 'M'
            periods = 1

        # Aggregate data based on frequency
        if freq == 'D':
            # For daily forecasting, use daily data
            prophet_df = item_df[['Date', 'Usage']].copy()
        else:
            # For weekly/monthly, aggregate accordingly
            prophet_df = (item_df[['Date', 'Usage']]
                         .set_index('Date')
                         .resample(freq)
                         .sum()
                         .reset_index())

        prophet_df = prophet_df.rename({'Date': 'ds', 'Usage': 'y'}, axis='columns')

        yhat_cushioned = 0

        if not prophet_df.empty and prophet_df.shape[0] > 2:
            try:
                model = Prophet(
                    daily_seasonality=True if freq == 'D' else False,
                    weekly_seasonality=True,
                    yearly_seasonality=True if len(prophet_df) > 24 else False
                )
                
                model.fit(prophet_df)
                
                # Create future dataframe
                future = model.make_future_dataframe(periods=periods, freq=freq)
                forecast = model.predict(future)
                
                # Get the forecasted value
                if freq == 'D' and isinstance(forecast_period, int):
                    # For custom days, sum up the daily forecasts
                    forecast_values = forecast.tail(periods)['yhat'].sum()
                else:
                    # For weekly/monthly, get the last forecast
                    forecast_values = float(forecast.tail(1)['yhat'].iloc[0])
                
                yhat_cushioned = max(0, forecast_values * safety_cushion)
                
            except Exception as e:
                print(f"Prophet forecasting error for {item}: {e}")
                return 0.0

        return round(yhat_cushioned, 0)

    def forecast_monthly_stock_usage_with_prohet(self, item, safety_cushion=1.10):
        """
        Wrapper method for backward compatibility
        """
        return self.forecast_stock_usage_with_prophet(item, 'monthly', safety_cushion)

    def get_stock_data(self):
        """
        This method returns a preprocessed data from the stock database in a pandas dataframe form
        :return:
        """
        stock_sheet = self.gc.open_by_key("1qqI-9I99Kix2PS1ksUralHeFXoyaArN7ZXYmCnMDLA0")
        stock_wksheet = stock_sheet.worksheet("My Stock")

        columns = stock_wksheet.row_values(1)
        columns = [data.replace('"', '') for data in columns]

        rows = stock_wksheet.get_all_values()

        rows = [[re.sub('"', '', data) for data in row] for row in rows[1:]]

        stock_df = pd.DataFrame(rows)
        stock_df.columns = columns

        stock_df["Rate"] = stock_df["Rate"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Case Qty"] = stock_df["Case Qty"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Safety Stock_80_Sl"] = stock_df["Safety Stock_80_Sl"].replace(['', "null", 'nan'], np.nan).astype(
            float)
        stock_df["Reorder Point"] = stock_df["Reorder Point"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Daily Average"] = stock_df["Daily Average"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Daily Std"] = stock_df["Daily Std"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Sample Size"] = stock_df["Sample Size"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Last Issued (In Days)"] = stock_df["Last Issued (In Days)"].replace(['', "null", 'nan'],
                                                                                      np.nan).astype(float)
        stock_df["Current Balance"] = stock_df["Current Balance"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Ptn Qty"] = stock_df["Ptn Qty"].replace(['', "null", 'nan'], np.nan).astype(float)
        stock_df["Bundle Qty"] = stock_df["Bundle Qty"].replace(['', "null", 'nan'], np.nan).astype(float)

        return stock_df

    def get_issue_voucher(self):
        """
        This method returns a issues voucher in a pandas dataframe
        :return:
        """
        issue_voucher_sheet = self.gc.open_by_key("1y-I8V05Anud-j7VWaob3OaE9ubUEd7qqUkVFB0N942w")
        issue_voucher_wksheet = issue_voucher_sheet.worksheet("Issues")

        columns = issue_voucher_wksheet.row_values(1)
        data = issue_voucher_wksheet.get_all_values()

        data = [[str(x).replace('"', '') for x in record] for record in data]

        df = pd.DataFrame(data)
        df.columns = columns

        df.drop(0, axis="rows", inplace=True)
        df["Date"] = pd.to_datetime(df["Date"], format="%Y-%m-%d")
        df["Usage"] = df["Usage"].str.replace(",", "").astype(float)

        df = df.groupby(["Date", "Item name", "Category"], as_index=False).sum()

        df = df.loc[~(df["Dept"] == "FUNCTION"), :]

        df = df.loc[~(df["Item name"].isin(self.fiterout_dormant_stock())), :]

        return df

    def adjusted_and_replace_stock_name(self, df, dept_name):

        staff_replaceabless = {
            "OGBONO": 'OGBONO (STAFF)',
            'DRY FISH': 'DRY FISH (STAFF)',
            'SPAGHETTI': 'SPAGHETTI (STAFF)',
            'SUGAR (GRANULATED)': 'SUGAR (GRANULATED, STAFF)',
            'TIN TOMATO (2.2kg)': 'TIN TOMATO (2.2kg, STAFF)',
            'VEGETABLE OIL': 'VEGETABLE OIL (STAFF)',
            "CURRY POWDER": "CURRY (TIGER,250g, STAFF)",
            "CURRY POWDER": "CURRY POWDER (500g)",
            "ONGA SEASONING": "ONGA SEASONING (STAFF)",
            "RED OIL": "RED OIL (STAFF)",
            "STAR MAGGI": "STAR MAGGI (STAFF)"
        }

        general_replaceables = {
            "ORIGIN BITTER (SMALL)": "ORIGIN BITTERS SMALL",
            "SHWEPPES": "SCHWEPPES",
            "ORIJIN BIG": "ORIJIN BEER",
            "SEMOVITA": "SEMO",
            "ORIJIN BIG": "ORIJIN BEER",
            "YAM": "YAM TUBER",
            "4TH STREET (BIG)": "4TH STREET",
            "SKKY": "SKYY"
        }

        dept_df = df.loc[df["Dept"] == str(dept_name).strip(), :]
        dept_df["Item name"] = dept_df["Item name"].replace(staff_replaceabless)
        indices = dept_df.index.tolist()
        dept_name_items = dept_df["Item name"].tolist()

        for index, item in zip(indices, dept_name_items):
            df.loc[index, "Item name"] = item
        return df

    def fiterout_dormant_stock(self):
        """
        This method removes stocks that have not be bought or issued as the case may be in the past 60
        days (default value) from the issues' voucher. This is to analyse only relevant stocks.
        :return:
        """
        sheet = self.gc.open_by_key("12z2gPOzJcezdhe7UM5p96TiXe4tjgkW7MPkXOdFHxeM")
        worksheet = sheet.worksheet("Dormant Stock")

        values = worksheet.get_all_values()[1:]
        values = [x[0] for x in values]
        return values

    def get_stock_departmental_usage_proportions(self):
        """
        This method returns the dictionary usage proportions of stock by departments
        :return:
        """
        time.sleep(2)
        sheet = self.gc.open_by_key("1eA9byIBi5uD83UVGt9jaX4-35ugTuTt2nDnPjmdCut4")
        worksheet = sheet.worksheet("Proportions")
        data = worksheet.get_all_values()
        cleaned_data = [[str(cell).replace('"', "") for cell in row] for row in data]
        df = pd.DataFrame(data=cleaned_data[1:], columns=cleaned_data[0])
        df["Proportion"] = df["%_Proportion"].astype(float)

        # stock_dict = {}

        # for _, row in df.iterrows():
        #     stock_name = row['Item name']
        #     department_name = row['Dept']
        #     prop_value = row['Proportion']

        #     if stock_name not in stock_dict:
        #         stock_dict[stock_name] = {}

        #     if department_name not in stock_dict[stock_name]:
        #         stock_dict[stock_name][department_name] = {"Prop": prop_value}
        #     else:
        #         stock_dict[stock_name][department_name]["Prop"] += prop_value

        # return stock_dict

        all_items_dict = {}
        item_list = df["Item name"].unique().tolist()
        for item in [str(item).strip() for item in item_list]:

            item_df = df.loc[df["Item name"]==item,:]

            all_items_dict[item] = dict(zip(item_df["Dept"],item_df["Proportion"].astype(float)))

        return all_items_dict


    def get_possible_staff_food(self):
        staff_food_list = [
            'CRAYFISH (STAFF)',
            'STAR MAGGI (STAFF)',
            'DRY PEPPER',
            'RED OIL (STAFF)',
            'GARRI',
            'VEGETABLE OIL (STAFF)',
            'LOCAL BEANS (STAFF)',
            'SALT',
            'DRY FISH (STAFF)',
            'RICE (STAFF)',
            'SPAGHETTI (STAFF)',
            'TIN TOMATO (400g FOR STAFF)',
            'TOMATO FLAVOR SEASONING (CUBE)',
            'CURRY POWDER (500g)',
            'SUGAR (GRANULATED, STAFF)',
            'ONGA SEASONING (STAFF)',
            'KNORR CUBE',
            'TIN TOMATO (2.2kg, STAFF)',
            'CORN FLOUR',
            'OGBONO (STAFF)'
        ]

        return staff_food_list

    def process_procurement(self):
        """
        This method pulls purchases, processes it and returns it as a pandas dataframe.
        :return:
        """
        stock_workbook = self.gc.open_by_key("1sP-RF1JYTp6OAKhhd8aVLD_vx_iIsTD84nQB-SQocc8")
        stock_worksheet = stock_workbook.worksheet("Purchases")

        data_list = stock_worksheet.get_all_values()

        df = pd.DataFrame(data=data_list[1:], columns=data_list[0])
        df = df[['Date', 'Stock Name', 'Category', 'Qty_Received', 'Rate', 'Amount']]
        df.columns = ['Date', 'Item name', 'Category', 'Portion', 'Unit Cost', 'Total Amount']
        df["Date"] = pd.to_datetime(df["Date"])
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        df["Unit Cost"] = df["Unit Cost"].astype(float)
        df["Portion"] = df["Portion"].astype(float)
        df["Total Amount"] = df["Total Amount"].astype(float)

        df = df.dropna(thresh=4, axis=0)
        return df

    def process_batch_stock(self, batch_description, item_qty, item_cost):
        import re
        batch_description = batch_description.replace("Batch", "").strip()
        item_cost = str(item_cost)[:-2]

        print("Length of Item Cost " + str(len(item_cost)))
        if len(item_cost) > 4:
            item_cost = str(item_cost)[:2]
        else:
            item_cost = str(item_cost)[:1]
        item_qty = str(item_qty)
        match = re.match("\((.*)\)", batch_description)
        grp = match.group()
        cleaned_txt = re.sub("\(*\)*", "", grp)
        n_item_n_amt = cleaned_txt.split("X")[1].split("/")
        n_item_n_amt[0] = re.sub("\d+", str(item_qty), n_item_n_amt[0])
        n_item_n_amt[1] = re.sub("\d+", str(item_cost), n_item_n_amt[1])
        average_value = cleaned_txt.split("X")[0]
        return str("Batch(") + average_value + "X" + n_item_n_amt[0] + str("/") + n_item_n_amt[1] + str(")")

    def skip_item_for_purchase_sig_test(self, reorder_level, forcasted_qty):
        """This method checks if:
        a) The diff between the current balance of a stock is significantly greater that the predicted value.
                or
        b) The predicted value is NAN or
        c) The diff between the current balance and predicted value is a negative

        If either of the above is true, the stock will be skipped from the forcast.
        """

        if reorder_level - forcasted_qty < 0:
            return True
        try:
            ratio = (reorder_level / forcasted_qty)
        except ZeroDivisionError:
            ratio = (reorder_level / 1)
        deviation = abs(ratio - 1)
        significance_threshold = 0.05  # Deviation cut off of 5%
        if deviation < significance_threshold:
            return False
        else:
            return True

    def initialize_sheets_page(self):
        gc = self.gc
        self.sheet = gc.open_by_key("1powB6YQD3WzpgZowXR-vsB9h9g-4FKzJ5fzXlZqEB0k")
        self.chemicals_worksheet = self.sheet.worksheet("Chemicals & Detergents")
        self.staff_worksheet = self.sheet.worksheet("Staff Food")

    def create_market_list(self, forecast_period='monthly', selected_categories=None, excluded_items=None, x_items_limit=150):
        """
        Enhanced market list creation with flexible forecasting periods and category selection
        
        Parameters:
        - forecast_period: 'weekly', 'monthly', or number of days
        - selected_categories: list of categories to include (None for default selection)
        - excluded_items: list of items to exclude from selected categories
        - x_items_limit: maximum number of items to process
        """
        house_worksheet = self.sheet.worksheet("Zeccol Mkl")
        house_worksheet.batch_clear(["A4:E200"])
        self.chemicals_worksheet.batch_clear(["A4:E200"])
        self.staff_worksheet.batch_clear(["A4:E200"])

        stock_df = self.get_stock_data()
        issues_df = self.get_issue_voucher()

        ## Adjusted Issues voucher
        #issues_df = self.adjusted_and_replace_stock_name(issues_df, "STAFF FOOD")

        usage_proportions = self.get_stock_departmental_usage_proportions()
        chemicals = ["BLEACH", "IZAL", "LIQUID SOAP", "ODOUR CONTROL"]
        staff_food = self.get_possible_staff_food()

        time.sleep(1)
        extras_and_exceptions_stock_name_list = self.get_extras_and_exceptions_stock_name()

        # Use category-based selection if provided
        if selected_categories:
            top_items = self.get_items_by_categories(
                selected_categories=selected_categories,
                x_items=x_items_limit,
                excluded_items=excluded_items
            )
        else:
            # Use default method
            top_items = self.get_top_x_number_of_items_to_buy(x_items_limit)

        extras = extras_and_exceptions_stock_name_list
        top_items.extend(extras)

        items_to_buy = sorted(set(top_items))
        
        print(f"Processing {len(items_to_buy)} items with {forecast_period} forecasting...")

        for item in items_to_buy:
            time.sleep(3)

            item_df = issues_df.loc[issues_df["Item name"] == item, :].dropna()
            
            self.avg_col_freq = self.remove_outliers_col_freq(item_df)

            # Use the enhanced forecasting method
            item_mv = self.forecast_stock_usage_with_prophet(
                item=item, 
                forecast_period=forecast_period,
                safety_cushion=1.10
            )

            print(f"{item} ({forecast_period}) = {item_mv}")

            self.item_mv_remainder = None

            if np.isnan(item_mv) or item_mv == 0:
                if item in extras_and_exceptions_stock_name_list:
                    time.sleep(1)
                    gc = self.gc
                    extras_sheet = gc.open_by_key("19ePbzsPDeY38_gkSs4FT7nxUQWQczaJ1F5UNfGOOk1g")
                    extras_worksheet = extras_sheet.worksheet("Extras")
                    extras_values_list = extras_worksheet.get_all_values()
                    extras_values_list = [[str(x).replace('"', '') for x in record] for record in extras_values_list]

                    extras_df = pd.DataFrame(extras_values_list[1:], columns=extras_values_list[0])
                    extras_df["Amount"] = extras_df["Amount"].str.replace([',', ''], '').astype(float)
                    extras_df["Rate"] = extras_df["Rate"].str.replace([',', ''], '').astype(float)
                    extras_df["Buy"] = extras_df["Buy"].str.replace([',', ''], '').astype(float)

                    item = extras_df["Stock Name"].values[0]
                    reorder_level_str = str(extras_df["Current Bal"].values[0])
                    buy_str = str(extras_df["Buy"].values[0])
                    mkl_rate = extras_df["Rate"].values[0]
                    mkl_amt = extras_df["Amount"].values[0]

                    house_worksheet.append_rows([[item, reorder_level_str, buy_str, str(mkl_rate), str(mkl_amt)]])
                    continue
                else:
                    continue

            remainder_mv_avg = None

            if item in staff_food:
                if item in usage_proportions:
                    staff_proportion = usage_proportions[item]['STAFF FOOD']
                    house_proportion = 1 - staff_proportion

                    all_mv = item_mv
                    staff_item_mv = all_mv * staff_proportion
                    remainder_mv_avg = all_mv * house_proportion
                else:
                    staff_item_mv = item_mv * 0.3  # Default 30% for staff if no proportion data
                    remainder_mv_avg = item_mv * 0.7

                self._process_item_purchase(item, stock_df, staff_item_mv, self.staff_worksheet, chemicals)

            if remainder_mv_avg is not None:
                item_mv = remainder_mv_avg

            # Process for house or chemicals
            self._process_item_purchase(item, stock_df, item_mv, 
                                     self.chemicals_worksheet if item in chemicals else house_worksheet, 
                                     chemicals)

    def _process_item_purchase(self, item, stock_df, item_mv, target_worksheet, chemicals):
        """
        Helper method to process individual item purchase calculations
        """
        try:
            self.item_df = stock_df.loc[stock_df["Stock Name"] == item, :]
            
            if self.item_df.empty:
                return

            self.ptn_name = self.item_df["Ptn Name"].values[0]
            self.case_qty = self.item_df["Case Qty"].values[0]
            self.b_qty = self.item_df["Bundle Qty"].values[0]
            self.b_name = self.item_df["Bundle_qty Unit"].values[0]
            self.rate = self.item_df["Rate"].values[0]
            self.current_bal = float(self.item_df["Current Balance"].values[0])

            if self.current_bal < 0:
                self.current_bal = 0

            if self.current_bal >= item_mv:
                if self.skip_item_for_purchase_sig_test(self.current_bal, item_mv):
                    return

            if "Batch" in self.b_name:
                purchase_df = self.process_procurement()
                item_purchase_df = purchase_df.loc[purchase_df["Item name"] == item, :]
                real_purchase_item_df = item_purchase_df.loc[item_purchase_df["Total Amount"] > 1, :]
                if not real_purchase_item_df.empty:
                    last_three_purchase = real_purchase_item_df.tail(3)
                    mean_amt = round(last_three_purchase["Total Amount"].mean(), -3)
                    mean_received = math.ceil(last_three_purchase["Portion"].mean())
                    self.b_name = self.process_batch_stock(self.b_name, mean_received, mean_amt)

            # Calculate reorder level display
            if self.current_bal < self.b_qty:
                reorder_level_str = str(self.current_bal) + " " + str(self.ptn_name)
            elif self.current_bal > self.b_qty and self.b_qty == 1:
                reorder_level_str = str(self.current_bal // self.b_qty) + " " + str(self.b_name)
            else:
                reorder_level_str = str(self.current_bal // self.b_qty) + " " + str(self.b_name)

            print(f"Current Bal: {self.current_bal}")
            try:
                print(f"Forecast: {math.ceil(item_mv)}")
            except Exception as ex:
                pass

            # Calculate quantity needed
            needed_qty = math.ceil(item_mv - self.current_bal)
            if needed_qty < 0:
                needed_qty = math.ceil(abs(needed_qty))

            if self.case_qty == 1:
                buy = needed_qty / self.b_qty
                buy_flag = needed_qty / self.b_qty
            else:
                buy = needed_qty / self.case_qty
                buy_flag = needed_qty / self.b_qty

            if buy_flag < 0.5:
                buy = 0.5
            elif 0.5 <= buy_flag < 1:
                buy = 1

            buy = round(buy, 1)
            buy_str = str(buy) + f" {self.b_name}"

            # Calculate rates and amounts
            if self.case_qty == 1:
                mkl_rate = round((self.rate * self.b_qty), -2)
            else:
                if buy < self.b_qty:
                    buy_str = str(int(self.b_qty // buy)) + f" {self.b_name}"
                    buy = buy / self.b_qty
                mkl_rate = round((self.rate * self.case_qty * self.b_qty), -2)

            mkl_amt = round(mkl_rate * buy, 0)

            target_worksheet.append_rows([[item, str(reorder_level_str), buy_str, str(mkl_rate), str(mkl_amt)]])

        except Exception as e:
            print(f"Error processing {item}: {e}")


if __name__ == "__main__":
    mkl = MarketList()
    
    # Example usage with new features:
    
    # Weekly forecast for selected categories
    # mkl.create_market_list(
    #     forecast_period='weekly',
    #     selected_categories=['FOOD ITEM', 'BEVERAGE', 'CLEANING SUPPLY'],
    #     excluded_items=['RICE (OLD STOCK)', 'EXPIRED ITEMS'],
    #     x_items_limit=100
    # )
    
    # Monthly forecast (default behavior)
    mkl.create_market_list()