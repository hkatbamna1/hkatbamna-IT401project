# Encore

**Student:** Hardik Katbamna<br>
**Course:** IT 401<br>
**Assignment:** A1 — Customize & Extend the IT401 Project Template

## Project Overview

Encore (working title) is a concert-discovery web application that helps people find live music near them. It brings local event information into one place and is intended for music fans who want an easier way to discover shows based on location and musical taste. The application addresses the difficulty of searching across artist pages, venue calendars, and event sites to find relevant concerts.

## Preliminary Semester Project Concept

Encore will grow into a personalized live-music discovery and decision-support tool. The planned application will collect concert information from external sources, let users search and filter events, and recommend shows based on preferences such as location, travel distance, genre, artist, venue, date, and price.

Major features planned for the semester include:

- live concert information acquired from an external API;
- search, sorting, and multi-criteria filtering;
- saved user preferences and favorite events;
- personalized concert recommendations;
- persistent storage for concerts, venues, artists, and user selections; and
- useful event details and links that help users decide which show to attend.

This is a preliminary concept and may be refined after Module 2 as available APIs and information sources are evaluated.

## Current Features

- **Homepage:** A custom Encore landing page with a unique name, tagline, application description, branded graphic, featured concert cards, and calls to action.
- **Shared layout and navigation:** Reusable page title, navigation, stylesheet, and footer elements are defined in `templates/base.html`. The Home and About pages extend this layout.
- **About page:** Explains the application's purpose and the intended concert-discovery experience.
- **Responsive presentation:** Custom styling in `static/style.css` supports the application's visual identity and page layouts.
- **Automated route tests:** Pytest checks that the Home and About routes respond successfully and contain their expected content.
- **JSON data display:** Not yet implemented. A future Explore page will load concert records from a JSON file in `data/`.
- **Filtering functionality:** The homepage currently presents location and genre controls, but server-side filtering is not yet implemented. This will be connected to the JSON-backed Explore page.

## Information Model (Conceptual)

Encore will eventually manage information about live events and the people, places, and preferences connected to them. At this stage, this is a conceptual model rather than a database schema.

| Entity | Example attributes |
| --- | --- |
| Concert | title, date, start time, genre, price, ticket URL, description |
| Artist | name, genres, image, popularity, external links |
| Venue | name, street address, city, state, ZIP code, coordinates |
| User preference | home location, travel radius, preferred genres, favorite artists |
| Recommendation | concert, relevance score, matching reasons, generated date |

Artists perform at concerts, concerts take place at venues, and user preferences are compared with concert information to create recommendations.

## Project Structure

```text
.
|-- app.py                  # Flask application factory and entry point
|-- config.py               # Environment-based application configuration
|-- routes/
|   `-- main.py             # Home and About route definitions
|-- templates/
|   |-- base.html           # Shared page layout, navigation, and footer
|   |-- index.html          # Encore landing page
|   `-- about.html          # Application purpose and process
|-- static/
|   `-- style.css           # Site-wide styles and responsive layouts
|-- data/                   # Local JSON data files (to be added)
|-- models/                 # Future data models
|-- services/               # API, search, and AI service modules
|-- tests/
|   `-- test_app.py         # Flask route tests
`-- requirements.txt        # Python dependencies
```

## Installation and Setup

### Prerequisites

- Python 3.10 or newer
- Git

### 1. Clone the repository

```bash
git clone https://github.com/hkatbamna1/hkatbamna-IT401project.git
cd hkatbamna-IT401project
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Run the application

```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in a browser.

## Configuration

Configuration is defined in `config.py` and supports the following environment variables:

| Variable | Description |
| --- | --- |
| `SECRET_KEY` | Flask secret key |
| `API_KEY` | Key for a future external event API |
| `AI_SERVICE_API_KEY` | Key for a future AI service integration |
| `FLASK_ENV` | Application configuration: `development` or `production` |

Never commit real API keys or other secrets to the repository.

## Testing

With the virtual environment activated, run:

```bash
python -m pytest
```

## Screenshots

### Homepage

> docs/screenshots/homepage.png

### about page

> docs/screenshots/about.png

Screenshots are also available in screenshots.pdf

## Future Work for Assignment 2

Work for A2 will focus on implementing API and scraping functionality.
