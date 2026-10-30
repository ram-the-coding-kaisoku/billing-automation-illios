

from datetime import date, timedelta, datetime
from decimal import Decimal, InvalidOperation

from pathlib import Path

import logging

import json

import re

import sys

import time

import html



from playwright.sync_api import (

    sync_playwright,

    TimeoutError as PlaywrightTimeoutError,

)



# ============================================================

# CONFIGURATION

# ============================================================



DASHBOARD_URL = "http://98.70.32.193:8001/"
SUPPORTED_PROVIDERS = {
    "AWS": ("AWS", "Amazon Web Services"),
    "GCP": ("GCP", "Google Cloud", "Google Cloud Platform"),
    "Azure": ("Azure", "Microsoft Azure"),
}
DEFAULT_PROVIDER = "AWS"

CONFIG_FILE = Path(__file__).resolve().with_name("billing_config.json")


def load_client_config():
    """Load each client's provider assignments from the JSON config."""
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Required billing configuration file was not found: {CONFIG_FILE}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {CONFIG_FILE} at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(config, dict):
        raise ValueError(f"Billing configuration must be a JSON object: {CONFIG_FILE}")

    clients = config.get("clients")
    if not isinstance(clients, dict) or not clients or any(
        not isinstance(client, str)
        or not client.strip()
        or not isinstance(providers, list)
        or any(
            not isinstance(provider, str) or not provider.strip()
            for provider in providers
        )
        for client, providers in clients.items()
    ):
        raise ValueError(
            f"'clients' in {CONFIG_FILE} must map non-empty client names "
            "to lists of provider names."
        )

    return list(clients), clients


CLIENTS, CLIENT_PROVIDERS = load_client_config()


GRANULARITY = "Daily"



# Today minus 8 days through today minus 2 days, inclusive.

START_DATE = date.today() - timedelta(days=8)

END_DATE = date.today() - timedelta(days=2)



HEADLESS = True

ACTION_TIMEOUT_MS = 12000

REPORT_TIMEOUT_SECONDS = 45
CLIENT_OPTION_TIMEOUT_SECONDS = 15



OUTPUT_DIR = Path("billing_reports")

DEBUG_DIR = OUTPUT_DIR / "debug"



OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_DIR.mkdir(parents=True, exist_ok=True)



LOG_FILE = OUTPUT_DIR / "billing_automation.log"



# ============================================================

# LOGGING

# ============================================================



logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s %(levelname)s: %(message)s",

    handlers=[

        logging.FileHandler(LOG_FILE, encoding="utf-8"),

        logging.StreamHandler(sys.stdout),

    ],

)



logger = logging.getLogger("billing_report")





# ============================================================

# GENERAL HELPERS

# ============================================================



def safe_name(value):

    """Create a safe filename from a client name."""

    value = re.sub(r'[<>:"/**\\\**|?*]+', "_", value)

    return re.sub(r"\s+", "_", value.strip())





def find_visible(page, selectors):

    """Return the first visible locator from a selector list."""

    for selector in selectors:

        try:

            locator = page.locator(selector).first



            if locator.count() and locator.is_visible():

                return locator

        except Exception:

            continue



    return None





def wait_for_page(page):

    """Wait for navigation without requiring network-idle indefinitely."""

    try:

        page.wait_for_load_state(

            "domcontentloaded",

            timeout=10000,

        )

    except PlaywrightTimeoutError:

        pass



    try:

        page.wait_for_load_state(

            "networkidle",

            timeout=2500,

        )

    except PlaywrightTimeoutError:

        pass





def save_debug(page, client, reason, provider=None):

    """Save diagnostics if report extraction fails."""
    if provider and provider != DEFAULT_PROVIDER:
        name = f"{provider}_{name}"



    try:

        screenshot_path = DEBUG_DIR / f"{name}.png"

        html_path = DEBUG_DIR / f"{name}.html"

        text_path = DEBUG_DIR / f"{name}.txt"



        page.screenshot(

            path=str(screenshot_path),

            full_page=True,

            timeout=15000,

        )



        html_path.write_text(

            page.content(),

            encoding="utf-8",

        )



        try:

            body_text = page.locator("body").inner_text(

                timeout=5000

            )

        except Exception:

            body_text = ""



        text_path.write_text(

            f"Reason: {reason}\n"

            f"URL: {page.url}\n"

            f"Title: {page.title()}\n"

            f"Visible HTML tables: "

            f"{page.locator('table:visible').count()}\n"

            f"Visible iframes: "

            f"{page.locator('iframe:visible').count()}\n\n"

            f"{body_text[-12000:]}",

            encoding="utf-8",

        )



        logger.info(

            "Diagnostic files saved for %s in %s",

            client,

            DEBUG_DIR,

        )



    except Exception:

        logger.exception(

            "Could not save diagnostics for %s",

            client,

        )





# ============================================================

# PROVIDER SELECTION

# ============================================================



def normalize_provider(provider):
    """Normalize a supported provider name or dashboard label."""
    normalized = re.sub(r"[^a-z0-9]", "", provider.casefold())

    for canonical, labels in SUPPORTED_PROVIDERS.items():
        if any(
            normalized == re.sub(r"[^a-z0-9]", "", label.casefold())
            for label in (canonical, *labels)
        ):
            return canonical

    supported = ", ".join(SUPPORTED_PROVIDERS)
    raise ValueError(
        f"Unsupported provider {provider!r}. Choose one of: {supported}."
    )


def get_provider_jobs():
    """Return configured client/provider pairs, validating the full mapping."""
    jobs = []
    for client in CLIENTS:
        configured = CLIENT_PROVIDERS[client]
        if not configured:
            raise ValueError(f"No providers configured for client {client!r}.")

        providers = tuple(dict.fromkeys(normalize_provider(p) for p in configured))
        jobs.extend((client, provider) for provider in providers)

    return jobs


def select_provider(page, provider):
    """Select the requested cloud provider from the dashboard."""
    provider_labels = SUPPORTED_PROVIDERS[provider]
    normalized_labels = {
        re.sub(r"[^a-z0-9]", "", label.casefold())
        for label in (provider, *provider_labels)
    }

    def is_provider_option(option):
        return any(
            re.sub(r"[^a-z0-9]", "", value.casefold()) in normalized_labels
            for value in (option["text"], option["value"])
        )

    control = page.locator("#provider-ts-control").first
    selected = False

    if control.count():
        try:

            control.scroll_into_view_if_needed()

            control.click(timeout=ACTION_TIMEOUT_MS)

            page.wait_for_timeout(250)



            selectors = [

                "#provider-ts-dropdown .option",

                "#provider-ts-dropdown [role='option']",

                "[role='option']",

            ]



            for selector in selectors:

                options = page.locator(selector)



                for i in range(min(options.count(), 50)):

                    option = options.nth(i)



                    try:

                        if (
                            option.is_visible()
                            and is_provider_option(
                                {
                                    "text": option.inner_text().strip(),
                                    "value": option.get_attribute("data-value") or "",
                                }
                            )
                        ):
                            option.click(timeout=5000)
                            selected = True
                            break

                    except Exception:

                        continue

                else:

                    continue



                break



        except Exception as exc:
            logger.warning(
                "Custom provider selection attempt: %s",
                exc,

            )



    # Native select fallback.

    native = page.locator("#provider").first



    if not selected and native.count():
        try:

            options = native.locator("option").evaluate_all(

                """els => els.map(o => ({

                    text: o.textContent.trim(),

                    value: o.value

                }))"""

            )



            for option in options:

                if is_provider_option(option):
                    native.select_option(
                        value=option["value"],
                        timeout=5000,
                    )
                    selected = True
                    break



        except Exception as exc:

            logger.info(

                "Native provider selection skipped: %s",

                exc,

            )



    if not selected:
        raise RuntimeError(
            f"Could not select provider {provider!r}; it was not found "
            "in the dashboard provider options."
        )

    page.wait_for_timeout(500)
    logger.info("Provider selected: %s", provider)





# ============================================================

# DATE FILTERS

# ============================================================



def set_date(page, selectors, value, label):

    """Set an ISO date, supporting Flatpickr and ordinary inputs."""

    locator = find_visible(page, selectors)



    if not locator:

        raise RuntimeError(

            f"Could not find visible {label} input"

        )



    result = locator.evaluate(

        """(el, value) => {

            if (el._flatpickr) {

                el._flatpickr.setDate(value, true, "Y-m-d");

                return el.value;

            }



            const setter = Object.getOwnPropertyDescriptor(

                HTMLInputElement.prototype, "value"

            )?.set;



            if (setter) {

                setter.call(el, value);

            } else {

                el.value = value;

            }



            el.dispatchEvent(

                new Event("input", {bubbles: true})

            );



            el.dispatchEvent(

                new Event("change", {bubbles: true})

            );



            return el.value;

        }""",

        value,

    )



    logger.info(

        "%s requested=%s displayed=%s",

        label,

        value,

        result,

    )





def set_dates(page):

    start = START_DATE.isoformat()

    end = END_DATE.isoformat()



    set_date(

        page,

        [

            "#startDate",

            "input[name='startDate']",

            "input[name='start_date']",

        ],

        start,

        "Start date",

    )



    set_date(

        page,

        [

            "#endDate",

            "input[name='endDate']",

            "input[name='end_date']",

        ],

        end,

        "End date",

    )





# ============================================================

# GRANULARITY

# ============================================================



def select_granularity(page):

    """Find a native select containing the Daily option."""

    selects = page.locator("select")



    for i in range(selects.count()):

        select = selects.nth(i)



        try:

            options = select.locator("option").evaluate_all(

                """els => els.map(o => ({

                    text: o.textContent.trim(),

                    value: o.value

                }))"""

            )



            for option in options:

                if (

                    option["text"].lower() == GRANULARITY.lower()

                    or option["value"].lower() == GRANULARITY.lower()

                ):

                    select.select_option(

                        value=option["value"],

                        timeout=3000,

                    )



                    logger.info(

                        "Granularity selected: %s",

                        GRANULARITY,

                    )

                    return



        except Exception:

            continue



    logger.warning(

        "Could not locate a native granularity selector"

    )





# ============================================================

# CLIENT SELECTION

# ============================================================



def select_client(page, client):

    """Select a client and verify the native project value."""

    control = page.locator("#project-ts-control").first



    if not control.count():

        raise RuntimeError(

            "Client dropdown #project-ts-control not found"

        )



    control.scroll_into_view_if_needed()

    control.click(timeout=ACTION_TIMEOUT_MS)

    page.wait_for_timeout(250)



    search_input = page.locator(

        "#project-ts-control input, "

        "input#project-ts-control, "

        "input[placeholder*='project' i], "

        "input[placeholder*='client' i]"

    ).first



    try:

        if search_input.count() and search_input.is_visible():

            search_input.fill(client)

        else:

            control.press_sequentially(client)

    except Exception:

        logger.info(

            "Could not type in dropdown; checking visible options"

        )



    selectors = [
        "#project-ts-dropdown .option",

        "#project-ts-dropdown [role='option']",

        "[role='listbox'] [role='option']",

        ".ts-dropdown .option",

        "[role='option']",

    ]



    visible_options = []
    selected = False
    deadline = time.monotonic() + CLIENT_OPTION_TIMEOUT_SECONDS

    while time.monotonic() < deadline and not selected:
        visible_options = []

        for selector in selectors:
            options = page.locator(selector)

            for i in range(min(options.count(), 100)):
                option = options.nth(i)

                try:
                    if not option.is_visible():
                        continue

                    label = option.inner_text().strip()

                    if label and label not in visible_options:
                        visible_options.append(label)

                    if label == client:
                        option.scroll_into_view_if_needed()
                        option.click(timeout=5000)
                        selected = True
                        break

                except Exception:
                    continue

            if selected:
                break

        if not selected:
            page.wait_for_timeout(250)



    if selected:
        logger.info(
            "Client option found after waiting for provider-filtered options: %s",
            client,
        )
    else:
        logger.info(
            "Custom dropdown options after waiting %s seconds for %s: %s",
            CLIENT_OPTION_TIMEOUT_SECONDS,
            client,
            visible_options,
        )



    # Exact-text fallback.

    if not selected:

        matches = page.get_by_text(

            client,

            exact=True,

        )



        for i in range(min(matches.count(), 20)):

            item = matches.nth(i)



            try:

                if item.is_visible():

                    item.click(timeout=3000)

                    selected = True

                    break

            except Exception:

                continue



    if not selected:

        raise RuntimeError(

            f"Could not select client {client!r}. "

            f"Visible options: {visible_options}"

        )



    page.wait_for_timeout(350)



    native = page.locator("#project").first

    native_value = ""



    if native.count():

        try:

            native_value = native.input_value()



            native_options = native.locator("option").evaluate_all(

                """els => els.map(o => ({

                    label: o.textContent.trim(),

                    value: o.value,

                    selected: o.selected

                }))"""

            )



            logger.info(

                "After client click: client=%r, native value=%r, "

                "options=%s",

                client,

                native_value,

                native_options,

            )



        except Exception:

            logger.exception(

                "Could not inspect native client selection"

            )



    if native_value and native_value != client:

        raise RuntimeError(

            f"Client selection mismatch: expected {client!r}, "

            f"got {native_value!r}"

        )



    logger.info("Selected client: %s", client)





# ============================================================

# APPLY FILTERS AND WAIT FOR REPORT

# ============================================================



def click_apply(page):

    names = [

        "Apply",

        "Apply Filters",

        "Apply Filter",

        "Generate Report",

        "Generate",

        "Search",

        "Filter",

    ]



    for name in names:

        try:

            button = page.get_by_role(

                "button",

                name=name,

                exact=True,

            ).first



            if (

                button.count()

                and button.is_visible()

                and button.is_enabled()

            ):

                button.scroll_into_view_if_needed()

                button.click(timeout=5000)



                logger.info(

                    "Clicked report button: %s",

                    name,

                )

                return True



        except Exception:

            continue



    # Fallback for controls that are not semantic buttons.

    for name in names:

        try:

            matches = page.get_by_text(

                name,

                exact=True,

            )



            for i in range(min(matches.count(), 10)):

                item = matches.nth(i)



                if item.is_visible():

                    item.scroll_into_view_if_needed()

                    item.click(timeout=3000)



                    logger.info(

                        "Clicked report action text: %s",

                        name,

                    )

                    return True



        except Exception:

            continue



    logger.warning(

        "Could not find an Apply/Generate button"

    )

    return False





def wait_for_report(page, client):

    deadline = time.monotonic() + REPORT_TIMEOUT_SECONDS

    previous_signature = None

    stable_count = 0



    while time.monotonic() < deadline:

        try:

            table_count = page.locator("table:visible").count()



            grid_count = page.locator(

                "[role='grid']:visible, [role='table']:visible"

            ).count()



            body_text = page.locator("body").inner_text(

                timeout=2000

            )



            signature = (

                table_count,

                grid_count,

                body_text[-1500:],

            )



            if signature == previous_signature:

                stable_count += 1

            else:

                stable_count = 0



            previous_signature = signature



            if (table_count or grid_count) and stable_count >= 2:

                logger.info(

                    "Report stabilized for %s: tables=%s grids=%s",

                    client,

                    table_count,

                    grid_count,

                )

                return



            lower = body_text.lower()



            empty_messages = [

                "no data",

                "no records",

                "no results",

                "no billing",

                "no consumption",

                "failed to load",

                "error loading",

            ]



            if (

                any(message in lower for message in empty_messages)

                and stable_count >= 2

            ):

                logger.warning(

                    "Possible empty/error report for %s",

                    client,

                )

                return



        except Exception:

            pass



        page.wait_for_timeout(1000)



    logger.warning(

        "Timed out waiting for report content for %s",

        client,

    )





# ============================================================

# TABLE EXTRACTION

# ============================================================



def extract_table_text(table):

    lines = []

    rows = table.locator("tr")



    for row_index in range(rows.count()):

        cells = rows.nth(row_index).locator("th, td")

        values = []



        for cell_index in range(cells.count()):

            try:

                value = cells.nth(cell_index).inner_text().strip()

                value = re.sub(r"\s+", " ", value)

                values.append(value)

            except Exception:

                values.append("")



        if any(values):

            lines.append("\t".join(values))



    return "\n".join(lines)


def extract_styled_html(locator):
    """Preserve the dashboard's rendered styles when exporting its markup."""
    return locator.evaluate(
        """(element) => {
            const clone = element.cloneNode(true);
            const sourceElements = [element, ...element.querySelectorAll("*")];
            const cloneElements = [clone, ...clone.querySelectorAll("*")];
            const textProperties = [
                "background-color",
                "color",
                "font-family",
                "font-size",
                "font-style",
                "font-variant",
                "font-weight",
                "letter-spacing",
                "line-height",
                "text-align",
                "text-decoration",
                "text-transform",
            ];

            sourceElements.forEach((source, index) => {
                const computedStyle = window.getComputedStyle(source);
                const inlineStyle = textProperties
                    .map((property) =>
                        `${property}:${computedStyle.getPropertyValue(property)}`
                    )
                    .join(";");
                cloneElements[index].setAttribute("style", inlineStyle);
            });

            const tables = [
                ...(clone.matches("table") ? [clone] : []),
                ...clone.querySelectorAll("table"),
            ];
            tables.forEach((table) => {
                table.style.width = "100%";
                table.style.tableLayout = "auto";
                table.style.borderCollapse = "collapse";
                table.style.borderSpacing = "0";
                table.style.border = "1px solid #d9e0e7";
            });

            const cells = [
                ...(clone.matches("th, td") ? [clone] : []),
                ...clone.querySelectorAll("th, td"),
            ];
            cells.forEach((cell) => {
                cell.style.border = "1px solid #d9e0e7";
                cell.style.padding = "4px 6px";
                cell.style.boxSizing = "border-box";
                cell.style.verticalAlign = "middle";
                cell.style.whiteSpace = "nowrap";
            });

            return clone.outerHTML;
        }"""
    )


def extract_billing_table(page):

    """Extract the largest visible HTML table or a common grid fallback."""

    candidates = []

    tables = page.locator("table:visible")



    for i in range(tables.count()):

        table = tables.nth(i)



        try:

            row_count = table.locator("tr").count()



            if row_count < 2:

                continue



            text = extract_table_text(table)

            nonempty_rows = [

                line for line in text.splitlines()

                if line.strip()

            ]



            if len(nonempty_rows) < 2:

                continue



            cell_count = table.locator("th, td").count()

            score = row_count * 100 + cell_count + len(text) / 100



            candidates.append(

                (score, table, text)

            )



        except Exception:

            continue



    if candidates:

        candidates.sort(

            key=lambda item: item[0],

            reverse=True,

        )



        _, table, text = candidates[0]



        logger.info(

            "Extracted HTML table with %s rows",

            table.locator("tr").count(),

        )



        return {
            "text": text,
            "html": extract_styled_html(table),
            "source": "HTML table",
        }



    # Custom grid fallback.

    grid_selectors = [

        "[role='grid']:visible",

        "[role='table']:visible",

        ".ag-root:visible",

        ".ag-theme-alpine:visible",

        ".MuiDataGrid-root:visible",

        ".data-grid:visible",

        ".tabulator:visible",

        ".handsontable:visible",

        "[role='rowgroup']:visible",

        ".ag-center-cols-container:visible",

        ".MuiDataGrid-virtualScroller:visible",

        ".tabulator-table:visible",

    ]



    for selector in grid_selectors:

        roots = page.locator(selector)



        for i in range(roots.count()):

            root = roots.nth(i)



            try:

                text = root.inner_text(timeout=3000).strip()



                if len(text.splitlines()) >= 2 and len(text) > 30:

                    return {
                        "text": text,
                        "html": extract_styled_html(root),
                        "source": selector,
                    }



            except Exception:

                continue



    # Iframe fallback.

    for frame_index, frame in enumerate(page.frames):

        if frame == page.main_frame:

            continue



        try:

            frame_tables = frame.locator("table:visible")



            for i in range(frame_tables.count()):

                table = frame_tables.nth(i)

                text = extract_table_text(table)



                if len([

                    line for line in text.splitlines()

                    if line.strip()

                ]) >= 2:

                    return {
                        "text": text,
                        "html": extract_styled_html(table),
                        "source": f"iframe {frame_index}",
                    }



        except Exception:

            continue



    raise RuntimeError(

        "No visible billing table with header and data was found."

    )





# ============================================================

# REPORT HTML AND TEXT FILES
# ============================================================


def billing_change_summary(table_html):
    """Compare the latest two daily columns for the total and each service."""
    row_pattern = re.compile(r"<tr\b[^>]*>.*?</tr\s*>", re.IGNORECASE | re.DOTALL)
    cell_pattern = re.compile(
        r"<(th|td)\b([^>]*)>(.*?)</\1\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    date_columns = []
    total_row = None

    def cell_text(cell_html):
        text = re.sub(r"<[^>]+>", " ", cell_html)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    def parse_amount(value):
        value = value.replace(",", "")
        is_negative = value.startswith("(") and value.endswith(")")
        amount_text = re.sub(r"[^0-9.-]", "", value)
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", amount_text):
            return None
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            return None
        return -abs(amount) if is_negative else amount

    rows = []
    for row_match in row_pattern.finditer(table_html):
        cells = list(cell_pattern.finditer(row_match.group(0)))
        if not cells:
            continue

        rows.append((row_match, cells))
        dates_in_row = []
        for column_index, cell in enumerate(cells):
            match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", cell_text(cell.group(3)))
            if match:
                try:
                    dates_in_row.append((date.fromisoformat(match.group()), column_index))
                except ValueError:
                    continue

        if len(dates_in_row) >= 2:
            date_columns = sorted(dates_in_row)

    if len(date_columns) < 2:
        return None

    previous_date, previous_column = date_columns[-2]
    latest_date, latest_column = date_columns[-1]
    if latest_date <= previous_date:
        return None

    total_cells = None
    for row_match, cells in rows:
        if cell_text(cells[0].group(3)).casefold() == "total":
            total_row = row_match
            total_cells = cells
            break
    if (
        total_cells is None
        or previous_column >= len(total_cells)
        or latest_column >= len(total_cells)
    ):
        return None

    previous_amount = parse_amount(cell_text(total_cells[previous_column].group(3)))
    latest_amount = parse_amount(cell_text(total_cells[latest_column].group(3)))
    if previous_amount is None or latest_amount is None:
        return None

    difference = latest_amount - previous_amount
    service_increases = []
    for _, cells in rows:
        service_name = cell_text(cells[0].group(3))
        if (
            service_name.casefold() == "total"
            or previous_column >= len(cells)
            or latest_column >= len(cells)
        ):
            continue
        previous_service_amount = parse_amount(
            cell_text(cells[previous_column].group(3))
        )
        latest_service_amount = parse_amount(
            cell_text(cells[latest_column].group(3))
        )
        if (
            service_name
            and previous_service_amount is not None
            and latest_service_amount is not None
            and latest_service_amount > previous_service_amount
        ):
            service_increases.append(
                (service_name, latest_service_amount - previous_service_amount)
            )

    return {
        "previous_date": previous_date,
        "latest_date": latest_date,
        "difference": difference,
        "service_increases": sorted(
            service_increases,
            key=lambda item: item[1],
            reverse=True,
        ),
        "total_row": total_row,
        "total_cells": total_cells,
        "latest_column": latest_column,
    }


def build_total_change_observation(client, provider, table_html):
    """Return structured total and service increases for significant changes."""
    summary = billing_change_summary(table_html)
    if summary is None or summary["difference"] <= Decimal("1"):
        return None

    return {
        "client": client,
        "provider": provider,
        "previous_date": summary["previous_date"],
        "latest_date": summary["latest_date"],
        "total_increase": summary["difference"],
        "service_increases": summary["service_increases"],
    }


def format_observation_amount(amount, provider):
    """Format an observation amount in the provider's billing currency."""
    currency_symbol = "₹" if provider in ("GCP", "Azure") else "$"
    return f"{currency_symbol}{amount:.2f}"


def render_observation_table(observations):
    """Render structured billing observations as an Outlook-friendly HTML table."""
    headers = (
        "Client",
        "Provider",
        "Period",
        "Total increase",
        "Service",
        "Service increase",
    )
    header_html = "".join(
        '<th style="border:1px solid #bbb;padding:6px 8px;'
        'background:#f2f2f2;text-align:left;">'
        f"{html.escape(header)}</th>"
        for header in headers
    )

    rows = []
    for observation in observations:
        services = observation["service_increases"] or [
            ("No individual service increase detected", None)
        ]
        shared_values = (
            observation["client"],
            observation["provider"],
            (
                f"{observation['previous_date'].isoformat()} to "
                f"{observation['latest_date'].isoformat()}"
            ),
            format_observation_amount(
                observation["total_increase"],
                observation["provider"],
            ),
        )
        shared_cells = "".join(
            f'<td rowspan="{len(services)}" style="border:1px solid #bbb;'
            'padding:6px 8px;text-align:center;vertical-align:middle;">'
            f"{html.escape(str(value))}</td>"
            for value in shared_values
        )
        for index, (service_name, service_increase) in enumerate(services):
            cells = shared_cells if index == 0 else ""
            service_values = (
                service_name,
                (
                    format_observation_amount(
                        service_increase,
                        observation["provider"],
                    )
                    if service_increase is not None
                    else "-"
                ),
            )
            cells += "".join(
                '<td style="border:1px solid #bbb;padding:6px 8px;">'
                f"{html.escape(str(value))}</td>"
                for value in service_values
            )
            rows.append(f"<tr>{cells}</tr>")

    return (
        '<table style="border-collapse:collapse;font-family:Arial,sans-serif;'
        'font-size:10pt;width:100%;">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def write_report(client, data, provider):
    """Save individual HTML/TXT files and return a simple email section."""
    name = safe_name(client)
    start = START_DATE.isoformat()
    end = END_DATE.isoformat()
    provider_prefix = "" if provider == DEFAULT_PROVIDER else f"{provider}_"

    text_path = OUTPUT_DIR / f"{provider_prefix}{name}_{start}_to_{end}.txt"
    html_path = OUTPUT_DIR / f"{provider_prefix}{name}_{start}_to_{end}.html"

    generated_at = datetime.now().isoformat(timespec="seconds")

    # Keep detailed metadata in local files; do not include it in the email.
    text_header = (
        f"{provider} Billing Report\n"
        f"Client: {client}\n"
        f"Provider: {provider}\n"
        f"Granularity: {GRANULARITY}\n"
        f"Date range: {start} to {end}\n"
        f"Generated: {generated_at}\n"
        f"Source: {data['source']}\n"
        + "=" * 70
        + "\n\n"
    )

    text_path.write_text(text_header + data["text"] + "\n", encoding="utf-8")

    safe_client = html.escape(client)
    safe_provider = html.escape(provider)
    email_table_html = data["html"]

    # Email section contains only the client name and consumption table.
    report_section = f"""
    <div style="font-family:Arial,sans-serif;font-size:10pt;margin:0 0 20px 0;">
      <h3 style="font-size:12pt;margin-bottom:8px;">{safe_client} - {safe_provider}</h3>
      <p><b>Consumption</b></p>
      <div style="width:100%;overflow-x:auto;margin:8px 0 0 0;">{email_table_html}</div>
    </div>
    """

    full_html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{provider} Billing Report - {safe_client}</title>
<style>
body {{ font-family: Arial, sans-serif; font-size: 10pt; }}
</style>
</head>
<body>
{report_section}
</body>
</html>
"""

    html_path.write_text(full_html, encoding="utf-8")

    logger.info("Saved %s", text_path)
    logger.info("Saved %s", html_path)

    return [text_path, html_path], report_section
def open_outlook_draft(report_sections, observations):
    """Create a simple Outlook draft. Never sends automatically."""
    sender_address = "sriram.v@ilios.digital"

    try:
        import win32com.client
    except ImportError:
        logger.error(
            "Outlook draft not created because pywin32 is missing. "
            "Install using: py -m pip install pywin32"
        )
        return False

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        sender_account = next(
            (
                account
                for account in outlook.Session.Accounts
                if account.SmtpAddress.casefold() == sender_address.casefold()
            ),
            None,
        )
        if sender_account is None:
            raise RuntimeError(
                f"Outlook account {sender_address} is not configured in this profile."
            )
        mail.SendUsingAccount = sender_account

        today_text = date.today().strftime("%d-%b-%Y")
        mail.Subject = f"Daily Billing Report - {today_text}"

        embedded_reports = "\n".join(report_sections)
        if observations:
            observation_html = render_observation_table(observations)
        else:
            observation_html = (
                "<p>No client total increased by more than 1.00 in its "
                "billing currency compared with the previous day.</p>"
            )

        mail.HTMLBody = f"""
        <html>
        <body style="font-family:Arial,sans-serif;font-size:10pt;color:#222;">
          <p>Hi </p>
          <p>Please find the billing report for <b>{today_text}</b> below.</p>

          <p><b>Observation:</b></p>
          {observation_html}

          {embedded_reports}

          <p><b><i>Regards,<br>
          Sri Ram,<br>
          Database Administrator</i></b></p>
        </body>
        </html>
        """

        # Display for manual review only; never call mail.Send().
        mail.Display()

        logger.info(
            "Outlook draft opened with %s embedded report sections.",
            len(report_sections),
        )
        return True

    except Exception:
        logger.exception("Could not create Outlook draft")
        return False
def main():

    start = START_DATE.isoformat()

    end = END_DATE.isoformat()
    jobs = get_provider_jobs()



    logger.info("=" * 70)

    logger.info("Starting multi-cloud billing report automation")

    logger.info("Dashboard: %s", DASHBOARD_URL)

    logger.info("Date range: %s to %s", start, end)

    logger.info("Granularity: %s", GRANULARITY)

    logger.info("Configured client/provider reports: %s", len(jobs))



    successes = []

    failures = []

    report_sections = []
    observations = []



    with sync_playwright() as playwright:

        browser = playwright.chromium.launch(

            headless=HEADLESS

        )



        context = browser.new_context(

            viewport={

                "width": 1600,

                "height": 1000,

            },

            accept_downloads=True,

        )



        page = context.new_page()

        page.set_default_timeout(

            ACTION_TIMEOUT_MS

        )



        for index, (client, provider) in enumerate(jobs, start=1):

            logger.info(

                "[%s/%s] Processing %s (%s)",

                index,

                len(jobs),

                client,

                provider,

            )



            try:

                page.goto(

                    DASHBOARD_URL,

                    wait_until="domcontentloaded",

                    timeout=30000,

                )



                wait_for_page(page)



                select_provider(page, provider)

                set_dates(page)

                select_granularity(page)

                select_client(page, client)



                clicked = click_apply(page)



                if not clicked:

                    logger.warning(

                        "Apply button was not found for %s",

                        client,

                    )



                wait_for_report(page, client)



                try:

                    data = extract_billing_table(page)



                except Exception as exc:

                    logger.exception(

                        "Extraction failed for %s",

                        client,

                    )



                    save_debug(

                        page,

                        client,

                        str(exc),

                        provider,

                    )



                    raise



                _, report_section = write_report(
                    client,
                    data,
                    provider,
                )

                observation = build_total_change_observation(
                    client,
                    provider,
                    data["html"],
                )
                if observation:
                    observations.append(observation)

                report_sections.append(report_section)
                successes.append((client, provider))



            except Exception:

                logger.exception(

                    "Failed to process %s (%s)",

                    client,

                    provider,

                )



                failures.append((client, provider))



                try:

                    save_debug(

                        page,

                        client,

                        "Unhandled processing error",

                        provider,

                    )

                except Exception:

                    pass



        context.close()

        browser.close()



    # Save a summary for local records.

    summary_path = OUTPUT_DIR / (
        f"run_summary_{start}_to_{end}.txt"
    )

    summary_content = (
        "Multi-cloud Billing Automation Summary\n"
        f"Configured reports: {len(jobs)}\n"

        f"Date range: {start} to {end}\n"

        f"Completed: {datetime.now().isoformat(timespec='seconds')}\n\n"

        f"Successful client/provider reports ({len(successes)}):\n"

        + (

            "\n".join(f"- {client} ({provider})" for client, provider in successes)

            if successes else "- None"

        )

        + "\n\n"

        f"Failed client/provider reports ({len(failures)}):\n"

        + (

            "\n".join(f"- {client} ({provider})" for client, provider in failures)

            if failures else "- None"

        )

        + "\n"

    )



    summary_path.write_text(

        summary_content,

        encoding="utf-8",

    )



    logger.info("Successful client/provider pairs: %s", successes)
    logger.info("Failed client/provider pairs: %s", failures)

    logger.info("Run summary saved: %s", summary_path)



    # Open Outlook only if at least one report was extracted.

    # Reports are embedded in the body; no report files are attached.

    if report_sections:
        open_outlook_draft(report_sections, observations)

    else:

        logger.error(

            "No reports were extracted. "

            "Outlook draft was not opened."

        )



    logger.info("Automation completed.")





if __name__ == "__main__":

    main()
