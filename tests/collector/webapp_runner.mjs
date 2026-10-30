// Rechnet die Testfälle mit der Preislogik der Web-App (für test_webapp_pricing.py).
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , modulePath, inputPath] = process.argv;
const { evaluate } = await import(pathToFileURL(modulePath).href);
const { stations, cases } = JSON.parse(readFileSync(inputPath, "utf8"));

const results = cases.map(({ lat, lon, radius, filters, now }) => {
  const { offers, outlook, count } = evaluate(stations, lat, lon, radius, filters, now);
  return {
    offers: offers.map((o) => [o.stationId, o.price.kwh, o.distanceKm, o.until, o.nextPrice, o.price.minuteFee, o.price.sessionFee]),
    outlook: outlook && [outlook.offer.stationId, outlook.offer.price.kwh, outlook.start, outlook.end],
    count,
  };
});
process.stdout.write(JSON.stringify(results));
