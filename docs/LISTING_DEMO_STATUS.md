# Listing demo status

Depop is out. A photo plus “post this” fills Vinted, eBay, and Facebook, then stops before Upload, List it, or Publish.

## What works in the product

- One vision pass turns the photo into a title, description, price, condition, and category. Later fields are filled locally, with no model call per field.
- If the sentence already gives the price and condition (“crusty”, “worth 2 pounds”), those win. A bare “post this” asks those two questions before Go.
- Dropdowns are opened and the option is chosen. A recorded skill does the same, one menu at a time, and does not skip ahead to a later click.
- Vinted: brand opens and No Label is chosen for an unbranded item, then colour, condition, material (Ceramic), and parcel size.
- eBay: the first page types the title and clicks Search. If Continue is on the page, that is clicked before List it for free.
- Facebook: the listing-type page opens the item form. Category, condition, and colour dropdowns are used. A dropdown that already shows its value stays closed. Used–fair matches the dash Facebook shows. The audience step (`step=audience`) asks before Publish.
- Add photo is not clicked, so a system file dialog does not open. The photo attaches only when the page has a file field. If the title and price are filled but that field cannot be read, the run stays on the page and pauses.
- Tab moves focus when a step asks for it. Text fields are set directly.
- Saying “post this” opens that site’s sell form. A sign-in page pauses there; Resume continues after you sign in.
- Vinted menus named “Brand Select a brand” and “Condition Select condition” are opened and filled. One flash call picks the parcel size: a mug is Medium (a shoebox), not Small.

## Seen on the signed-in Chrome window

- Facebook Marketplace is on the last step: blue mug, £2, condition Used–fair, and a Publish button. That button was not clicked. The form on screen was filled by hand, not by a Taskwipe run.
- eBay has been on List it for free. That button was not clicked.
- One Vinted listing was posted earlier by a manual Upload on an already filled form. That does not count as the product stopping at the confirm.

## Not done yet

The product itself has not yet been watched filling a form and stopping on the confirm on two sites. The side panel in Chrome is still the previous build until Taskwipe is reloaded from `chrome://extensions`. Reload once, then Deal with this on a signed-in Vinted, eBay, or Facebook tab.
