## TeslaController

A Python program to control my home Tesla Powerwall, focusing on optimising buying and selling energy to reduce carbon impact.

## How it works

The program utilises weather forecasts to predict how much solar energy my local area will get prior to 4pm to determine the output of the solar panels for that day.
Using this information, the machine learning model predicts how much the Tesla Powerwall needs to import from the National Grid to be fully charged by 4pm. Taking into account
our daily energy usage, this model achieves an accuracy of ~90%.

More of this data can be seen on my website at: https://www.powerdashboard.co.uk
Check it out and let me know your thoughts!

For any enquiries please contact me at tberesfords@outlook.com

## Key Information

  - Between 2am and 5am, electricity can be bought at its lowest rate. This is also when the energy is usually at its lowest carbon intensity due to low demand.
  - Between 4pm and 7pm, electricity can be sold at its highest rate. Energy is usually at its highest carbon intensity in this period due to high demand.
  - Any electricity bought between 2am and 5am can be sold for a profit between 4pm and 7pm, as well as offsetting CO2.

## License

[MIT](https://choosealicense.com/licenses/mit/)
