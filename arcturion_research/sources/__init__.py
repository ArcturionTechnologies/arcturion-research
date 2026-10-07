"""Source adapters. Each module exposes search-style functions that return
lists of dicts with at least title, url and snippet. Keyed adapters read their
key from an environment variable and raise http.MissingKey when it is unset."""
