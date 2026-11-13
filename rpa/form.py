"""Minimal HTML form/error extraction built on html.parser."""

from html.parser import HTMLParser


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.errors = []
        self._current_form = None
        self._current_error = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._current_form = {
                "action": attrs.get("action", ""),
                "method": attrs.get("method", "get").lower(),
                "inputs": [],
            }
            self.forms.append(self._current_form)
        elif tag == "input" and self._current_form is not None:
            self._current_form["inputs"].append(
                {
                    "name": attrs.get("name", ""),
                    "value": attrs.get("value", ""),
                    "type": attrs.get("type", "text"),
                    "maxlength": attrs.get("maxlength"),
                    "min": attrs.get("min"),
                    "max": attrs.get("max"),
                }
            )
        elif tag == "textarea" and self._current_form is not None:
            self._current_form["inputs"].append(
                {
                    "name": attrs.get("name", ""),
                    "value": "",
                    "type": "textarea",
                    "maxlength": attrs.get("maxlength"),
                    "min": None,
                    "max": None,
                }
            )
        elif tag == "p" and "error" in attrs.get("class", "").split():
            self._current_error = {
                "field": attrs.get("data-field", ""),
                "value": attrs.get("data-value", ""),
                "message": "",
            }
            self.errors.append(self._current_error)

    def handle_endtag(self, tag):
        if tag == "form":
            self._current_form = None
        elif tag == "p":
            self._current_error = None

    def handle_data(self, data):
        if self._current_error is not None:
            self._current_error["message"] += data


class Page:
    def __init__(self, forms, errors):
        self.forms = forms
        self.errors = errors


def parse_page(body):
    parser = _PageParser()
    parser.feed(body)
    return Page(parser.forms, parser.errors)


def find_form(page, action):
    for form in page.forms:
        if form["action"] == action:
            return form
    return None


def find_input(form, name):
    for field in form["inputs"]:
        if field["name"] == name:
            return field
    return None


def hidden_value(form, name):
    field = find_input(form, name)
    return field["value"] if field else None


def first_error(page):
    if page.errors:
        return page.errors[0]
    return None

