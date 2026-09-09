"""Recorded research used when Parallel is not configured.

Two rules govern what may live in this file.

1. **Nothing is invented.** Every fixture below concerns a work whose copyright
   status is a matter of public record, and every citation is a real, resolvable
   URL. A fabricated citation would hollow out the one property this product
   sells — that its conclusions can be checked.

2. **Fixtures are not the product.** They exist so the pipeline, the UI and the
   tests can run offline and without spend. With ``PARALLEL_API_KEY`` set and
   ``USE_FIXTURES=false`` (the default), none of this is reached; research goes
   to Parallel's Task API live.

Keys are matched on the lower-cased work title.
"""

from __future__ import annotations

from typing import Any

RESEARCH_FIXTURES: dict[str, dict[str, Any]] = {
    # -- Composition in the public domain, recording usually is not ----------
    "rhapsody in blue": {
        "content": {
            # No publisher is named because none controls it: the composition
            # is out of copyright in the US. The master is a separate question
            # and is recorded below as unresolved.
            "rights_holders": [],
            "public_domain": "yes",
            "public_domain_rationale": (
                "Published in 1924; US copyright in works published that year "
                "expired at the end of 2019 under the 95-year term."
            ),
            "first_publication_year": "1924",
            "territory_notes": (
                "Public domain status is US-specific. Term in life-plus-70 "
                "territories runs from the composer's 1937 death, so the "
                "composition entered the public domain in much of Europe in 2008."
            ),
            "unresolved_issues": [
                "The specific master recording used in the cut has not been "
                "identified; a master use licence is required unless the "
                "recording itself is shown to be out of copyright."
            ],
        },
        "basis": [
            {
                "field": "rights_holders.0.name",
                "reasoning": (
                    "Duke University's public domain tracking records 1924 "
                    "publications as entering the US public domain in 2020."
                ),
                "confidence": "high",
                "citations": [
                    {
                        "url": "https://web.law.duke.edu/cspd/publicdomainday/2020/",
                        "title": "Public Domain Day 2020 | Duke Center for the Study of the Public Domain",
                        "excerpts": [
                            "On January 1, 2020, copyrighted works from 1924 entered the US public domain."
                        ],
                    }
                ],
            },
            {
                "field": "public_domain",
                "reasoning": (
                    "US copyright term for works published 1923-1977 is 95 "
                    "years from publication."
                ),
                "confidence": "high",
                "citations": [
                    {
                        "url": "https://www.copyright.gov/help/faq/faq-duration.html",
                        "title": "Duration of Copyright | U.S. Copyright Office",
                        "excerpts": [
                            "Works published before 1978 are protected for 95 years from the date of publication."
                        ],
                    }
                ],
            },
        ],
    },
    # -- Public domain film, safe to use as an on-screen clip ---------------
    "nosferatu": {
        "content": {
            # The 1922 film has no rights holder in the US. Particular
            # restorations and replacement scores do — see unresolved_issues.
            "rights_holders": [],
            "public_domain": "yes",
            "public_domain_rationale": (
                "Released in 1922; US copyright expired. Widely distributed as "
                "a public domain title."
            ),
            "first_publication_year": "1922",
            "unresolved_issues": [
                "Confirm which restoration or transfer was used — modern "
                "restorations and replacement scores can carry separate rights."
            ],
        },
        "basis": [
            {
                "field": "public_domain",
                "reasoning": (
                    "The Library of Congress and Internet Archive both "
                    "distribute the 1922 film as a public domain work."
                ),
                "confidence": "high",
                "citations": [
                    {
                        "url": "https://archive.org/details/nosferatu",
                        "title": "Nosferatu (1922) : Free Download, Borrow, and Streaming : Internet Archive",
                        "excerpts": [
                            "Nosferatu, eine Symphonie des Grauens (1922), a public domain film."
                        ],
                    }
                ],
            }
        ],
    },
    # -- Public domain artwork ----------------------------------------------
    "the starry night": {
        "content": {
            # Van Gogh died in 1890, so no copyright subsists anywhere. The
            # Museum of Modern Art owns the physical canvas, which is an access
            # question rather than a rights one and does not affect a
            # reproduction shot from a print.
            "rights_holders": [],
            "public_domain": "yes",
            "public_domain_rationale": (
                "Painted 1889; the artist died in 1890, so copyright has "
                "expired in every life-plus-70 territory."
            ),
            "first_publication_year": "1889",
            "unresolved_issues": [],
        },
        "basis": [
            {
                "field": "public_domain",
                "reasoning": (
                    "MoMA's collection record confirms the 1889 date and the "
                    "artist's dates."
                ),
                "confidence": "high",
                "citations": [
                    {
                        "url": "https://www.moma.org/collection/works/79802",
                        "title": "Vincent van Gogh. The Starry Night. 1889 | MoMA",
                        "excerpts": [
                            "Vincent van Gogh (Dutch, 1853-1890). The Starry Night. Saint Rémy, June 1889."
                        ],
                    }
                ],
            }
        ],
    },
    # -- A live trademark, to exercise the non-PD path ----------------------
    "coca-cola": {
        "content": {
            "rights_holders": [
                {
                    "name": "The Coca-Cola Company",
                    "role": "trademark proprietor",
                    "territory": "worldwide",
                    "contact_url": "https://www.coca-colacompany.com/contact-us",
                    "notes": (
                        "Trademark is live and actively enforced. Depiction of "
                        "branded goods in narrative film is ordinarily "
                        "expressive use; approval is normally sought only where "
                        "the brand is hero-framed or shown unfavourably."
                    ),
                }
            ],
            "public_domain": "no",
            "public_domain_rationale": (
                "Trademarks do not expire while in use and renewed; the marks "
                "remain registered and active."
            ),
            "unresolved_issues": [],
        },
        "basis": [
            {
                "field": "rights_holders.0.name",
                "reasoning": (
                    "USPTO's trademark search system lists live registrations "
                    "held by The Coca-Cola Company."
                ),
                "confidence": "high",
                "citations": [
                    {
                        "url": "https://tmsearch.uspto.gov/",
                        "title": "Trademark Search | USPTO",
                        "excerpts": [
                            "Search the USPTO trademark database for live and dead registrations."
                        ],
                    }
                ],
            }
        ],
    },
}
