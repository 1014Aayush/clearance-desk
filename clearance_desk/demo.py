"""A worked example: one reel of a fictional independent feature.

'Nightshift' is invented, and so is every shot described here. The *works*
referenced in it are real and deliberately chosen so that their copyright
status is publicly checkable — a 1924 composition, a 1922 film, an 1889
painting, a live trademark. That mix exercises every branch of the rules engine
against facts a reviewer can independently verify, which a set of made-up songs
by made-up artists could not.

Used by the demo run, the offline UI and the end-to-end tests.
"""

from __future__ import annotations

from .models import (
    Category,
    Confidence,
    Identifiability,
    Prominence,
    ProjectMeta,
    RiskItem,
    Source,
)


def demo_project() -> ProjectMeta:
    return ProjectMeta(
        title="Nightshift",
        cut_label="rough cut v4",
        runtime_seconds=94 * 60,
        distribution_intent="worldwide, all media, in perpetuity",
        territories=["worldwide"],
        delivery_date="2026-11-14",
    )


def demo_items() -> list[RiskItem]:
    """A reel's worth of spotted material, covering every outcome the engine has."""
    return [
        # Composition is out of copyright; the recording almost certainly is not.
        # This is the trap that catches productions who check only one side.
        RiskItem(
            id="itm_demo_01",
            category=Category.MUSIC_SYNC,
            title="Rhapsody in Blue",
            known_work="Rhapsody in Blue",
            known_year=1924,
            description="Plays on the diner's radio while Mara counts the till.",
            source=Source.VIDEO,
            start_seconds=862,
            end_seconds=931,
            prominence=Prominence.FEATURED,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            audible_in_clear=True,
            detection_confidence=Confidence.HIGH,
            evidence="Clarinet glissando audible in the clear from 14:22.",
        ),
        # Ordinary background branding — expressive use, no paperwork.
        RiskItem(
            id="itm_demo_02",
            category=Category.TRADEMARK,
            title="Coca-Cola",
            description="Red can sits on the counter, label partly turned away.",
            source=Source.VIDEO,
            start_seconds=191,
            end_seconds=204,
            prominence=Prominence.BACKGROUND,
            identifiability=Identifiability.PARTIAL,
            detection_confidence=Confidence.HIGH,
            evidence="Contour bottle silhouette and red spot colour.",
        ),
        # The same brand, but hero-framed and treated badly. Same mark, very
        # different legal position.
        RiskItem(
            id="itm_demo_03",
            category=Category.TRADEMARK,
            title="Coca-Cola",
            description=(
                "Vending machine fills frame; Dov kicks it and the logo is "
                "centre-frame as it topples."
            ),
            source=Source.VIDEO,
            start_seconds=2711,
            end_seconds=2729,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            depicted_negatively=True,
            detection_confidence=Confidence.HIGH,
            evidence="Full logo legible for 11 seconds during the assault beat.",
        ),
        # Public domain artwork, hero-framed. Prominence must not matter here.
        RiskItem(
            id="itm_demo_04",
            category=Category.ARTWORK,
            title="The Starry Night",
            known_work="The Starry Night",
            known_year=1889,
            description="Framed print above Mara's bed, held for a slow push-in.",
            source=Source.VIDEO,
            start_seconds=3320,
            end_seconds=3339,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            detection_confidence=Confidence.HIGH,
            evidence="Full reproduction fills the frame for 19 seconds.",
        ),
        # Public domain film — but which transfer? Restorations carry their own
        # rights, so the item cannot be signed off until that is pinned down.
        RiskItem(
            id="itm_demo_05",
            category=Category.FILM_TV_CLIP,
            title="Nosferatu",
            known_work="Nosferatu",
            known_year=1922,
            description="Playing on the motel television behind the dialogue.",
            source=Source.VIDEO,
            start_seconds=4102,
            end_seconds=4160,
            prominence=Prominence.FEATURED,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            detection_confidence=Confidence.MEDIUM,
            evidence="Orlok's staircase shadow, recognisable at 1:08:22.",
        ),
        # Nobody can license what nobody can identify.
        RiskItem(
            id="itm_demo_06",
            category=Category.ARTWORK,
            title="Untitled alley mural",
            description=(
                "Large unattributed mural behind the actors through the whole "
                "alley confrontation."
            ),
            source=Source.VIDEO,
            start_seconds=2890,
            end_seconds=3050,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            no_published_identity=True,
            detection_confidence=Confidence.HIGH,
            evidence="Mural occupies the left third of frame for 2m40s.",
        ),
        RiskItem(
            id="itm_demo_07",
            category=Category.PERSON_LIKENESS,
            title="Man at counter",
            description="Background diner patron, face turned from camera.",
            source=Source.VIDEO,
            start_seconds=870,
            end_seconds=920,
            prominence=Prominence.INCIDENTAL,
            identifiability=Identifiability.NOT_IDENTIFIABLE,
            detection_confidence=Confidence.MEDIUM,
            evidence="Back of head only; no facial features visible.",
        ),
        RiskItem(
            id="itm_demo_08",
            category=Category.PERSON_LIKENESS,
            title="Woman at the bar",
            description="Non-speaking extra, face clearly in focus for six seconds.",
            source=Source.VIDEO,
            start_seconds=1544,
            end_seconds=1550,
            prominence=Prominence.BACKGROUND,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            detection_confidence=Confidence.HIGH,
            evidence="Sharp three-quarter profile, well lit.",
        ),
        RiskItem(
            id="itm_demo_09",
            category=Category.MUSIC_LIBRARY,
            title="Slow Tension Bed 04",
            description="Score bed under the alley sequence.",
            source=Source.CUE_SHEET,
            start_seconds=2890,
            end_seconds=3050,
            prominence=Prominence.BACKGROUND,
            identifiability=Identifiability.PARTIAL,
            detection_confidence=Confidence.HIGH,
            evidence="Listed on the composer's cue sheet as library.",
        ),
        RiskItem(
            id="itm_demo_10",
            category=Category.SCRIPT_REFERENCE,
            title="Acme Savings & Loan",
            description="Named in dialogue as where Mara's father banked.",
            source=Source.SCRIPT,
            scene="INT. DINER - NIGHT (p.34)",
            prominence=Prominence.BACKGROUND,
            detection_confidence=Confidence.HIGH,
            evidence='MARA: "Dad kept everything at Acme Savings."',
        ),
        RiskItem(
            id="itm_demo_11",
            category=Category.SIGNAGE_PRINT,
            title="Local newspaper front page",
            description="Newspaper on the counter; masthead readable in a cutaway.",
            source=Source.VIDEO,
            start_seconds=940,
            end_seconds=942,
            prominence=Prominence.INCIDENTAL,
            identifiability=Identifiability.NOT_IDENTIFIABLE,
            detection_confidence=Confidence.LOW,
            evidence="Two-second cutaway, masthead out of focus.",
        ),
        RiskItem(
            id="itm_demo_12",
            category=Category.LOCATION,
            title="Bellweather Motel",
            description="Distinctive neon frontage used for three exterior scenes.",
            source=Source.VIDEO,
            start_seconds=4080,
            end_seconds=4102,
            prominence=Prominence.FEATURED,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            release_on_file=True,
            detection_confidence=Confidence.HIGH,
            evidence="Signage legible; location agreement already executed.",
        ),
    ]
