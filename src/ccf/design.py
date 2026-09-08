"""Semantic Apple-inspired design tokens for the desktop UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    window: str
    sidebar: str
    content: str
    surface: str
    elevated: str
    text: str
    secondary_text: str
    tertiary_text: str
    separator: str
    border: str
    button: str
    button_hover: str
    selection: str
    accent: str
    accent_hover: str
    accent_pressed: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str


LIGHT = ThemePalette(
    window="#EEF3FA",
    sidebar="#F4F7FC",
    content="#FBFCFE",
    surface="#FFFFFF",
    elevated="#FDFEFF",
    text="#162238",
    secondary_text="#607089",
    tertiary_text="#8D9AB0",
    separator="#DFE6F1",
    border="#CBD6E6",
    button="#F1F5FB",
    button_hover="#E6EEF9",
    selection="#E3F0FF",
    accent="#247BFF",
    accent_hover="#126BEA",
    accent_pressed="#0757C9",
    success="#2C9A67",
    success_soft="#EAF8F0",
    warning="#C78319",
    warning_soft="#FFF5DD",
    danger="#D14D55",
    danger_soft="#FDECEE",
)


DARK = ThemePalette(
    window="#0D1624",
    sidebar="#111E2D",
    content="#152235",
    surface="#1B2B40",
    elevated="#22364D",
    text="#F3F7FC",
    secondary_text="#A9B9CC",
    tertiary_text="#778BA3",
    separator="#2A3D54",
    border="#38516C",
    button="#203249",
    button_hover="#2A405A",
    selection="#164372",
    accent="#55A7FF",
    accent_hover="#79B9FF",
    accent_pressed="#348DEB",
    success="#66D096",
    success_soft="#183A32",
    warning="#E7B85A",
    warning_soft="#493B1D",
    danger="#FF8587",
    danger_soft="#4A2730",
)


def palette_for(dark: bool) -> ThemePalette:
    return DARK if dark else LIGHT
