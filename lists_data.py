# Lists, fee table and currencies taken from the Pokemon & Graphics Card Reselling Tracker workbook (GPU model lists excluded).
DATA = {
 "lists": {
  "pokemon_sets": [
   "Base Set",
   "Jungle",
   "Fossil",
   "Team Rocket",
   "Gym Heroes",
   "Gym Challenge",
   "Neo Genesis",
   "Neo Discovery",
   "Neo Revelation",
   "Neo Destiny",
   "Legendary Collection",
   "Expedition Base Set",
   "Aquapolis",
   "Skyridge",
   "EX Ruby & Sapphire",
   "EX Sandstorm",
   "EX Dragon",
   "EX Team Magma vs Team Aqua",
   "EX Hidden Legends",
   "EX FireRed & LeafGreen",
   "EX Team Rocket Returns",
   "EX Deoxys",
   "EX Emerald",
   "EX Unseen Forces",
   "EX Delta Species",
   "EX Legend Maker",
   "EX Holon Phantoms",
   "EX Crystal Guardians",
   "EX Dragon Frontiers",
   "EX Power Keepers",
   "Diamond & Pearl",
   "Mysterious Treasures",
   "Secret Wonders",
   "Great Encounters",
   "Majestic Dawn",
   "Legends Awakened",
   "Stormfront",
   "Platinum",
   "Rising Rivals",
   "Supreme Victors",
   "Arceus",
   "HeartGold & SoulSilver",
   "Unleashed",
   "Undaunted",
   "Triumphant",
   "Call of Legends",
   "Black & White",
   "Emerging Powers",
   "Noble Victories",
   "Next Destinies",
   "Dark Explorers",
   "Dragons Exalted",
   "Boundaries Crossed",
   "Plasma Storm",
   "Plasma Freeze",
   "Plasma Blast",
   "Legendary Treasures",
   "XY Base",
   "Flashfire",
   "Furious Fists",
   "Phantom Forces",
   "Primal Clash",
   "Roaring Skies",
   "Ancient Origins",
   "BREAKthrough",
   "BREAKpoint",
   "Fates Collide",
   "Steam Siege",
   "Evolutions",
   "Sun & Moon Base",
   "Guardians Rising",
   "Burning Shadows",
   "Crimson Invasion",
   "Ultra Prism",
   "Forbidden Light",
   "Celestial Storm",
   "Dragon Majesty",
   "Lost Thunder",
   "Team Up",
   "Detective Pikachu",
   "Unbroken Bonds",
   "Unified Minds",
   "Hidden Fates",
   "Cosmic Eclipse",
   "Sword & Shield Base",
   "Rebel Clash",
   "Darkness Ablaze",
   "Champion's Path",
   "Vivid Voltage",
   "Shining Fates",
   "Battle Styles",
   "Chilling Reign",
   "Evolving Skies",
   "Celebrations",
   "Fusion Strike",
   "Brilliant Stars",
   "Astral Radiance",
   "Pokemon Go",
   "Lost Origin",
   "Silver Tempest",
   "Crown Zenith",
   "Scarlet & Violet Base",
   "Paldea Evolved",
   "Obsidian Flames",
   "151",
   "Paradox Rift",
   "Paldean Fates",
   "Temporal Forces",
   "Twilight Masquerade",
   "Shrouded Fable",
   "Stellar Crown",
   "Surging Sparks",
   "Prismatic Evolutions",
   "Journey Together",
   "Destined Rivals",
   "Black Bolt",
   "White Flare",
   "Mega Evolution",
   "Phantasmal Flames",
   "Ascended Heroes",
   "Perfect Order",
   "Chaos Rising",
   "Pitch Black",
   "30th Celebration",
   "Delta Reign",
   "Other / Not Listed"
  ],
  "categories": [
   "Pokemon Card",
   "Graphics Card"
  ],
  "conditions": [
   "Sealed / Unopened",
   "New / Sealed",
   "Mint / Near Mint",
   "Excellent",
   "Open Box",
   "Used - Like New",
   "Lightly Played",
   "Used - Good",
   "Moderately Played",
   "Used - Fair",
   "Heavily Played",
   "Damaged",
   "For Parts / Not Working"
  ],
  "grading": [
   "Raw / Ungraded",
   "PSA 10",
   "PSA 9",
   "PSA 8",
   "PSA 7",
   "PSA 6 or lower",
   "BGS 10 Black Label",
   "BGS 9.5",
   "BGS 9",
   "CGC 10",
   "CGC 9.5",
   "SGC 10",
   "TAG 10",
   "Other Grading Co."
  ],
  "purchase_sources": [
   "eBay",
   "Facebook Marketplace",
   "Local Card/PC Shop",
   "Card Show / Convention",
   "Garage Sale / Estate Sale",
   "Distributor / Wholesale",
   "Craigslist / OfferUp",
   "Whatnot Live",
   "Microcenter / Retail Store",
   "Trade",
   "Other"
  ],
  "sale_platforms": [
   "eBay",
   "TCGplayer",
   "Whatnot",
   "Mercari",
   "Facebook Marketplace",
   "COMC",
   "Troll and Toad",
   "Local / Cash Sale",
   "Amazon",
   "Other"
  ],
  "receipt_categories": [
   "Inventory Purchase",
   "Shipping Supplies",
   "Shipping Postage",
   "Grading Fees",
   "Software / Subscriptions",
   "Storage / Display Supplies",
   "Marketing / Advertising",
   "Booth / Show Fees",
   "Mileage / Travel",
   "Other"
  ],
  "payment_methods": [
   "Credit Card",
   "Debit Card",
   "PayPal",
   "Cash",
   "Venmo/CashApp",
   "Check",
   "Other"
  ]
 },
 "fees": [
  {
   "platform": "eBay",
   "fee_pct": 0.1335,
   "fixed": 0.3,
   "processing": "Yes",
   "notes": "~13.25% final value fee + $0.30 per order (standard categories, avg)"
  },
  {
   "platform": "TCGplayer",
   "fee_pct": 0.105,
   "fixed": 0.3,
   "processing": "Yes",
   "notes": "~9.5% marketplace fee + ~2.5% payment processing + $0.30"
  },
  {
   "platform": "Whatnot",
   "fee_pct": 0.08,
   "fixed": 0.0,
   "processing": "Yes",
   "notes": "8% selling fee, payment processing included"
  },
  {
   "platform": "Mercari",
   "fee_pct": 0.1,
   "fixed": 0.0,
   "processing": "Yes",
   "notes": "10% selling fee + payment processing included"
  },
  {
   "platform": "Facebook Marketplace",
   "fee_pct": 0.05,
   "fixed": 0.0,
   "processing": "Yes",
   "notes": "5% selling fee (shipped items), $0 local cash sales"
  },
  {
   "platform": "COMC",
   "fee_pct": 0.15,
   "fixed": 0.0,
   "processing": "Yes",
   "notes": "Approx. blended commission, varies by payout method"
  },
  {
   "platform": "Troll and Toad",
   "fee_pct": 0.15,
   "fixed": 0.0,
   "processing": "Yes",
   "notes": "Approx. consignment/marketplace rate"
  },
  {
   "platform": "Local / Cash Sale",
   "fee_pct": 0.0,
   "fixed": 0.0,
   "processing": "N/A",
   "notes": "No platform fees on in-person cash deals"
  },
  {
   "platform": "Amazon",
   "fee_pct": 0.15,
   "fixed": 0.0,
   "processing": "Yes",
   "notes": "Category referral fee, approx."
  },
  {
   "platform": "Other",
   "fee_pct": 0.0,
   "fixed": 0.0,
   "processing": "N/A",
   "notes": "Update manually for the specific platform"
  }
 ],
 "currencies": [
  {
   "code": "USD",
   "name": "US Dollar",
   "rate": 1.0
  },
  {
   "code": "CAD",
   "name": "Canadian Dollar",
   "rate": 0.73
  },
  {
   "code": "GBP",
   "name": "British Pound",
   "rate": 1.27
  },
  {
   "code": "EUR",
   "name": "Euro",
   "rate": 1.08
  },
  {
   "code": "JPY",
   "name": "Japanese Yen",
   "rate": 0.0067
  },
  {
   "code": "AUD",
   "name": "Australian Dollar",
   "rate": 0.65
  },
  {
   "code": "MXN",
   "name": "Mexican Peso",
   "rate": 0.052
  }
 ],
 "visual": [
  {
   "group": "Pokemon Card Eras",
   "era": "Scarlet & Violet era (2023-2026)",
   "highlights": "151, Paradox Rift, Prismatic Evolutions, Surging Sparks",
   "url": "https://www.google.com/search?tbm=isch&q=pokemon+tcg+scarlet+violet+sets"
  },
  {
   "group": "Pokemon Card Eras",
   "era": "Sword & Shield era (2020-2022)",
   "highlights": "Evolving Skies, Brilliant Stars, Lost Origin, Crown Zenith",
   "url": "https://www.google.com/search?tbm=isch&q=pokemon+tcg+sword+shield+sets"
  },
  {
   "group": "Pokemon Card Eras",
   "era": "Sun & Moon era (2017-2019)",
   "highlights": "Hidden Fates, Cosmic Eclipse, Team Up",
   "url": "https://www.google.com/search?tbm=isch&q=pokemon+tcg+sun+moon+sets"
  },
  {
   "group": "Pokemon Card Eras",
   "era": "XY & Black/White era (2011-2016)",
   "highlights": "Evolutions, Roaring Skies, Plasma Storm",
   "url": "https://www.google.com/search?tbm=isch&q=pokemon+tcg+xy+black+white+sets"
  },
  {
   "group": "Pokemon Card Eras",
   "era": "Classic / Vintage (1999-2010)",
   "highlights": "Base Set, Jungle, Fossil, Neo Genesis, EX series",
   "url": "https://www.google.com/search?tbm=isch&q=pokemon+tcg+base+set+vintage+cards"
  },
  {
   "group": "Pokemon Card Eras",
   "era": "Special / Crossover releases",
   "highlights": "Pokemon Go, Celebrations, Dragon Majesty, Detective Pikachu",
   "url": "https://www.google.com/search?tbm=isch&q=pokemon+tcg+special+set+cards"
  }
 ]
}
