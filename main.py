import pandas as pd
import requests
import teslapy as tp
import time
import os
import datetime
from datetime import datetime, timedelta
import logging
import mysql.connector
from dotenv import load_dotenv
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score

logging.basicConfig(level=logging.INFO)
load_dotenv()

EMAIL = os.getenv("EMAIL")
API_KEY = os.getenv("API_KEY")
ADDRESS = os.getenv("ADDRESS")

# Change these details to match your exchange rates and periods
BUY_LOW = 2     # Start time at which you can buy electricity cheap
STOP_BUY = 5    # End time at which you can buy electricity cheap
SELL_HIGH = 16  # Start time at which you can sell electricity high
MIN_BATTERY_RESERVE = 20  # Your minimum battery reserve - personal preference

MAX_RETRIES = 30
RETRY_DELAY = 12
FILENAME = "battery_data.xlsx"


def get_seconds_until_next_hour(target_hour):
    now = datetime.now()
    target_time = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if now >= target_time:
        target_time += timedelta(days=1)

    delta = target_time - now
    logging.info(f"Now waiting till {target_hour}am.\n")
    return max(1, delta.total_seconds())  # avoid returning 0


class TeslaBattery:
    def __init__(self):
        self.tesla = tp.Tesla(EMAIL)
        if not self.tesla.authorized:
            self.authorise_account()
        self.battery = self.tesla.battery_list()[0]
        self.mydb = mysql.connector.connect(
            host=os.getenv("HOST"),
            user=os.getenv("USER"),
            password=os.getenv("PASSWORD"),
            database=os.getenv("DATABASE"),
            port=os.getenv("PORT")
        )
        self.database_df = None

    def authorise_account(self):
        print('Use browser to login. Page Not Found will be shown at success.')
        print('Open this URL: ' + self.tesla.authorization_url())
        self.tesla.fetch_token(authorization_response=input('Enter URL after authentication: '))

    def get_battery_info(self):
        site_info_dict = dict(self.battery.get_site_data())
        battery_reserve = dict(self.battery.get_site_info())['backup_reserve_percent']
        return (
            {
                "timestamp": datetime.fromisoformat(site_info_dict['timestamp'][:-1] + '+00:00'),
                "backup_reserve": battery_reserve,
                "percentage_charged": round(site_info_dict['percentage_charged']),
                "battery_charge_input": -(site_info_dict['battery_power'] / 1000),
                "load": site_info_dict['load_power'] / 1000
            }
        )

    def get_battery_energy(self):
        return self.battery.get_calendar_history_data(kind="energy", end_date="2023-09-14T00:00:00-15:00", period='day')

    def get_battery_power(self):
        return self.battery.get_calendar_history_data(kind='power', end_date="2023-09-14T00:00:00-15:00")

    def set_backup_reserve_and_log(self, percent):
        try:
            if percent <= MIN_BATTERY_RESERVE:
                percent = MIN_BATTERY_RESERVE
            elif percent >= 100:
                percent = 100
            self.battery.set_backup_reserve_percent(percent)
        except Exception as e:
            logging.error(f"Error setting backup reserve: {e}")

    def ensure_db_connection(self):
        """Ensure that the database is connected. If not, attempt to reconnect."""
        retries = 0
        while retries < MAX_RETRIES:
            try:
                if not self.mydb.is_connected():
                    self.mydb.connect()
                self.mydb.ping(reconnect=True, attempts=3, delay=2)
                logging.info(f"Database connectivity is {self.mydb.is_connected()}")
                return
            except mysql.connector.Error as err:
                logging.error(f"Failed to connect to the database. Attempt {retries + 1}/{MAX_RETRIES}. Error: {err}")
                retries += 1
                time.sleep(RETRY_DELAY)
        if retries >= MAX_RETRIES:
            self.set_backup_reserve_and_log(100)
            logging.error("System failure. Review other logs. Manually overriden to 100%")
            quit()

    def close_connection(self):
        self.mydb.close()

    def set_database_data_as_df(self):
        self.ensure_db_connection()
        with self.mydb.cursor() as cursor:
            statement = """
                SELECT TS, PV FROM embeddedForecast WHERE TS > 'DATE';
                """
            statement = statement.replace("DATE", str(datetime.today().date()))
            cursor.execute(statement)
            data = cursor.fetchall()
            self.database_df = pd.DataFrame(data, columns=['Timestamp', 'PV_Value'])
        self.close_connection()

    def calculate_backup_reserve(self, data):
        return predicting_solar_generation(data)

    def forecast_backup_reserve(self):
        grouped_by_date_pv_data = self.database_df.groupby(self.database_df['Timestamp'].dt.date)
        values = []
        for i in grouped_by_date_pv_data['PV_Value']:
            values.append([i[0], sum(i[1]), round(((-0.00003 * sum(i[1])) + 1.95) * 100)])
        forecasted_percentages = pd.DataFrame(values,
                                              columns=['Date', 'Total Generation', 'Battery Reserve Percentage'])
        forecasted_percentages.to_excel('battery_data.xlsx')


def complete_task(task, error_msg, delay):
    try:
        return task
    except Exception as e:
        logging.error(f"{error_msg}: {e}")
        time.sleep(delay)


def maintain_loop(tesla_object):
    start = BUY_LOW
    stop = STOP_BUY
    value = MIN_BATTERY_RESERVE
    initial_setting = False
    while True:
        curtime = datetime.now()
        if curtime.hour == start and not initial_setting:
            weather_data = get_weather_data()
            value = complete_task(tesla_object.calculate_backup_reserve(weather_data),
                                  "Error calculating backup reserve", 300)
            complete_task(tesla_object.set_backup_reserve_and_log(value), "Error setting backup reserve", 300)
            if tesla_object.get_battery_info()['backup_reserve'] <= MIN_BATTERY_RESERVE:
                tesla_object.set_backup_reserve_and_log(MIN_BATTERY_RESERVE)
            logging.info(f"Backup-reserve was set to {tesla_object.get_battery_info()['backup_reserve']}")
            initial_setting = True
            time.sleep(120)
        elif (curtime.hour >= stop) or tesla_object.get_battery_info()['percentage_charged'] >= value:
            tesla_object.set_backup_reserve_and_log(MIN_BATTERY_RESERVE)
            logging.info(f"Backup-reserve was reset to {MIN_BATTERY_RESERVE}%")
            initial_setting = False
            time.sleep(get_seconds_until_next_hour(start))
        else:
            time.sleep(300)


def get_powerwall_data():
    location = "./Power wall/"
    file_list = os.listdir(location)
    df_list = []

    for file in file_list:
        if file.endswith('.csv'):  # Ensure it's a CSV file
            df_temp = pd.read_csv(location + file)
            df_list.append(df_temp)

    df = pd.concat(df_list, ignore_index=True)
    df = df.sort_values(by="Date time")
    df['Date time'] = pd.to_datetime(df['Date time'])
    mask = (df['Date time'].dt.hour >= STOP_BUY) & (df['Date time'].dt.hour < SELL_HIGH)
    filtered_df = df[mask]
    agg_df_v2 = filtered_df.groupby(filtered_df['Date time'].dt.date).agg({'Solar (kW)': 'sum', 'Energy Remaining (%)': ['first', 'last']}).reset_index()
    agg_df_v2['Solar (kW)'] = agg_df_v2['Solar (kW)'] / 10
    return agg_df_v2


def get_weather_data():
    response = requests.get(f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{ADDRESS}?unitGroup=metric&key={API_KEY}&contentType=json")
    days = response.json()['days']
    weather_log = []
    for day in days:
        day_dict = {'datetime': day['datetime'], 'solar_energy': 0, 'solar_radiation': 0, 'temp': 0.0, 'cloud_cover':
            0.0, 'visibility': 0.0}
        for hour in day['hours'][STOP_BUY:SELL_HIGH+1]:
            day_dict['solar_energy'] += hour['solarenergy']
            day_dict['solar_radiation'] += hour['solarradiation']
            day_dict['temp'] += hour['temp']
            day_dict['cloud_cover'] += hour['cloudcover']
            day_dict['visibility'] += hour['visibility']
        day_dict['cloud_cover'] = day_dict['cloud_cover'] / 11
        weather_log.append(day_dict)
    df = pd.DataFrame(weather_log)
    exporting_weather_data(df)
    return df


def exporting_weather_data(df):
    date = df['datetime'][0]
    df.to_csv(os.path.join("./Forecasts", f"{date}_weather_data_with_forecasts.csv"))


def model_prediction():
    df = pd.read_csv("model_data.csv")
    X = df[['solar_energy', 'cloud_cover']]
    y = df['Solar (kW)']

    # Creating a linear regression model
    model = LinearRegression()
    model.fit(X, y)

    # Predicting solar generation
    y_pred = model.predict(X)

    # Printing coefficients and intercept
    print(f"Intercept (β0): {model.intercept_}")
    print(f"Coefficient for solar_energy (β1): {model.coef_[0]}")
    print(f"Coefficient for cloud_cover (β2): {model.coef_[1]}")

    # Model evaluation
    mse = mean_squared_error(y, y_pred)
    rmse = mean_squared_error(y, y_pred, squared=False)
    r2 = r2_score(y, y_pred)

    print(f"\nMean Squared Error (MSE): {mse}")
    print(f"Root Mean Squared Error (RMSE): {rmse}")
    print(f"R-squared: {r2}")


def predicting_solar_generation(data):
    intercept = 7.233580040904478
    solar_energy_coefficient = 2.349053450659037
    cloud_cover_coefficient = 0.08670980527942238
    y = intercept + solar_energy_coefficient * data['solar_energy'][0] - cloud_cover_coefficient * data['cloud_cover'][0]
    target_charge = 120 - (4.18 * y)
    return round(target_charge)


if __name__ == "__main__":
    tb = TeslaBattery()
    tb.ensure_db_connection()  # Test database on start-up
    tb.close_connection()
    # exporting_weather_against_battery_data()
    maintain_loop(tb)
    # model_prediction()
