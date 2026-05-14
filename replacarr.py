#!/usr/bin/env python3
"""
replacarr - Automatically upgrade quality of recently watched movies
Watches Plex for recently played movies and triggers Radarr to replace
low-quality files with better versions based on quality profile settings.
"""

import os
import json
import logging
import sys
import time
import asyncio 
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

# ============================================================================
# Setup
# ============================================================================

# Load environment variables from .env file
load_dotenv("/app/config/.env")

# Create logs directory if it doesn't exist
LOG_DIR = Path("/app/logs")
LOG_DIR.mkdir(exist_ok=True)

# Setup logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "replacarr.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("replacarr")

# ============================================================================
# Configuration Validation
# ============================================================================

def get_config() -> dict:
    """Load and validate configuration from environment variables."""
    
    required_vars = [
        "PLEX_URL",
        "PLEX_TOKEN", 
        "RADARR_URL",
        "RADARR_API_KEY",
        "DESIRED_QUALITY"
    ]
    
    config = {}
    missing_vars = []
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        else:
            config[var.lower()] = value
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please check your .env file")
        sys.exit(1)
    
    # Optional settings with defaults
    config["recent_days"] = int(os.getenv("RECENT_DAYS", "7"))
    config["max_replacements_per_run"] = int(os.getenv("MAX_REPLACEMENTS_PER_RUN", "3"))
    config["skip_hours"] = int(os.getenv("SKIP_HOURS", "24"))
    
    # Validate quality value
    valid_qualities = ["480p", "720p", "1080p", "4k"]
    if config["desired_quality"].lower() not in valid_qualities:
        logger.error(f"Invalid DESIRED_QUALITY: {config['desired_quality']}")
        logger.error(f"Must be one of: {', '.join(valid_qualities)}")
        sys.exit(1)
    
    logger.info(f"Configuration loaded successfully")
    logger.info(f"  Plex URL: {config['plex_url']}")
    logger.info(f"  Radarr URL: {config['radarr_url']}")
    logger.info(f"  Desired quality: {config['desired_quality']}")
    logger.info(f"  Recent days: {config['recent_days']}")
    logger.info(f"  Max replacements per run: {config['max_replacements_per_run']}")
    logger.info(f"  Skip hours: {config['skip_hours']}")
    
    return config

# ============================================================================
# Quality Helper Functions
# ============================================================================

def extract_resolution(quality_string: str) -> str:
    """
    Extract resolution from Radarr quality string.
    Examples: "Bluray-1080p" -> "1080p", "WEBDL-720p" -> "720p", "SDTV" -> "480p"
    """
    if not quality_string:
        return "Unknown"
    
    quality_lower = quality_string.lower()
    
    # Check for 4K first
    if "4k" in quality_lower or "2160p" in quality_lower:
        return "4k"
    # Check for 1080p
    elif "1080p" in quality_lower:
        return "1080p"
    # Check for 720p
    elif "720p" in quality_lower:
        return "720p"
    # DVD, SDTV, etc. are 480p
    elif "dvd" in quality_lower or "sdtv" in quality_lower or "480p" in quality_lower:
        return "480p"
    
    return "Unknown"

def get_quality_rank(resolution: str) -> int:
    """Get numeric rank for quality comparison."""
    ranks = {
        "4k": 4,
        "1080p": 3,
        "720p": 2,
        "480p": 1,
        "Unknown": 0
    }
    return ranks.get(resolution.lower(), 0)

def needs_upgrade(current_quality: str, desired_quality: str) -> Tuple[bool, str]:
    """
    Check if current quality needs upgrade based on desired quality.
    Returns (should_upgrade, reason)
    """
    current_res = extract_resolution(current_quality)
    desired_res = desired_quality.lower()
    
    current_rank = get_quality_rank(current_res)
    desired_rank = get_quality_rank(desired_res)
    
    if current_rank == 0:
        return True, f"Current quality unknown/unspecified (will replace)"
    
    if current_rank < desired_rank:
        return True, f"Current {current_res} < desired {desired_res}"
    else:
        return False, f"Current {current_res} >= desired {desired_res} (no upgrade needed)"

# ============================================================================
# Plex Client
# ============================================================================

class PlexClient:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self.headers = {
            "Accept": "application/json",
            "X-Plex-Token": token,
            "X-Plex-Product": "replacarr",
            "X-Plex-Client-Identifier": "replacarr"
        }
    
    async def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Plex."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.url}/identity", headers=self.headers)
                response.raise_for_status()
                return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def _extract_tmdb_from_item(self, item: dict) -> Optional[int]:
        """
        Extract TMDb ID from a Plex metadata item's Guid fields.
            Returns int TMDb ID or None if not found.
    """
        guids = item.get("Guid", [])
        for guid in guids:
            guid_id = guid.get("id", "")
            if guid_id.startswith("tmdb://"):
                try:
                    tmdb_id = int(guid_id.replace("tmdb://", ""))
                    logger.debug(f"  Found TMDb ID in Plex: {tmdb_id}")
                    return tmdb_id
                except ValueError:
                    logger.debug(f"  Failed to parse TMDb ID from: {guid_id}")
                    continue
        return None

    async def get_tmdb_mapping(self) -> Dict[str, int]:
        """
        Scan Plex library sections and build mapping of ratingKey -> TMDb ID.
        Returns dict: {rating_key: tmdb_id}
        """
        rating_to_tmdb = {}
    
        # Get all library sections
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.url}/library/sections",
                    headers=self.headers
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch Plex sections for TMDb mapping: {e}")
            return rating_to_tmdb
    
        sections = data.get("MediaContainer", {}).get("Directory", [])
        movie_sections = [s for s in sections if s.get("type") == "movie"]
    
        logger.debug(f"Found {len(movie_sections)} movie sections to scan for TMDb IDs")
    
        for section in movie_sections:
            section_id = section.get("key")
            section_title = section.get("title", "Unknown")
        
            if not section_id:
                continue
        
            logger.debug(f"Scanning section '{section_title}' (ID: {section_id}) for TMDb IDs...")
        
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    response = await client.get(
                        f"{self.url}/library/sections/{section_id}/all",
                        headers=self.headers
                    )
                    response.raise_for_status()
                    data = response.json()
            except Exception as e:
                logger.warning(f"Failed to fetch items from section {section_id}: {e}")
                continue
        
            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            logger.debug(f"  Section has {len(metadata)} items")
        
            for item in metadata:
                rating_key = str(item.get("ratingKey"))
                if not rating_key:
                    continue
            
                tmdb_id = self._extract_tmdb_from_item(item)
            
                if tmdb_id:
                    rating_to_tmdb[rating_key] = tmdb_id
                    logger.debug(f"  Mapped: {item.get('title')} ({rating_key}) -> TMDb: {tmdb_id}")
    
        logger.info(f"TMDb mapping complete: {len(rating_to_tmdb)} movies have TMDb IDs in Plex")
        return rating_to_tmdb
    
    async def get_recently_played_movies(self, days_back: int, tmdb_mapping: Dict[str, int]) -> List[Dict]:
        """
        Get movies played in the last X days with pagination (no 1000 cap).
        Returns list of dicts with title, year, tmdb_id (if available), play_count, last_viewed
        """
        cutoff_time = int(time.time()) - (days_back * 86400)
    
        all_history = []
        start = 0
        page_size = 1000
    
        logger.debug(f"Fetching play history with pagination (cutoff: {days_back} days ago)")
    
        while True:
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.get(
                        f"{self.url}/status/sessions/history/all?allUsers=1&X-Plex-Container-Start={start}&X-Plex-Container-Size={page_size}",
                        headers=self.headers
                    )
                    response.raise_for_status()
                    data = response.json()
            except Exception as e:
                logger.error(f"Failed to fetch Plex play history page (start={start}): {e}")
                break
        
            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            if not metadata:
                logger.debug(f"No metadata returned for page start={start}, stopping")
                break
        
            all_history.extend(metadata)
            page_num = (start // page_size) + 1
            logger.debug(f"Fetched page {page_num}: {len(metadata)} entries (total so far: {len(all_history)})")
        
            # Check if we've fetched everything
            total_size = data.get("MediaContainer", {}).get("totalSize", 0)
            if total_size > 0 and len(all_history) >= total_size:
                logger.debug(f"Reached totalSize={total_size}, stopping pagination")
                break
            if len(metadata) < page_size:
                logger.debug(f"Last page had fewer than {page_size} entries, stopping pagination")
                break
        
            start += len(metadata)
    
        logger.info(f"Fetched {len(all_history)} total history entries from Plex")
    
        # Process history entries
        play_stats: Dict[str, Dict] = {}
    
        for item in all_history:
            # Only process movies
            if item.get("type") != "movie":
                continue
        
            title = item.get("title", "")
            year = item.get("year", "")
        
            if not title:
                logger.debug(f"Skipping item with no title: {item.get('ratingKey')}")
                continue
        
            rating_key = str(item.get("ratingKey", ""))
            viewed_at = item.get("viewedAt", 0)
        
            if viewed_at < cutoff_time:
                logger.debug(f"Skipping '{title} ({year})' - last viewed before cutoff")
                continue
        
            # Try to get TMDb ID from mapping (if available)
            tmdb_id = tmdb_mapping.get(rating_key)
        
            # Create unique key (use TMDb ID if available, otherwise title|year)
            if tmdb_id:
                movie_key = f"tmdb_{tmdb_id}"
            else:
                movie_key = f"{title}|{year}" if year else title
        
            if movie_key not in play_stats:
                play_stats[movie_key] = {
                    "title": title,
                    "year": year,
                    "tmdb_id": tmdb_id,
                    "rating_key": rating_key,
                    "play_count": 0,
                    "last_viewed": 0
                }
        
            play_stats[movie_key]["play_count"] += 1
        
            if viewed_at > play_stats[movie_key]["last_viewed"]:
                play_stats[movie_key]["last_viewed"] = viewed_at
        
            logger.debug(f"  Recorded play: '{title} ({year})' (TMDb: {tmdb_id}) - viewed at {viewed_at}")
    
        # Convert to list format
        result = list(play_stats.values())
    
        # Count how many have TMDb IDs vs title/year only
        with_tmdb = sum(1 for m in result if m.get("tmdb_id"))
        without_tmdb = len(result) - with_tmdb
    
        logger.info(f"Found {len(result)} movies played in the last {days_back} days")
        logger.info(f"  - {with_tmdb} movies have TMDb IDs in Plex metadata")
        logger.info(f"  - {without_tmdb} movies will use title/year matching")
    
        # Log first few for debugging
        for movie in result[:5]:
            last_viewed_date = datetime.fromtimestamp(movie["last_viewed"]).strftime("%Y-%m-%d")
            tmdb_info = f"TMDb:{movie['tmdb_id']}" if movie.get("tmdb_id") else "title/year only"
            logger.debug(f"  '{movie['title']} ({movie['year']})' - {tmdb_info}, {movie['play_count']} plays, last: {last_viewed_date}")
    
        return result

# ============================================================================
# Radarr Client
# ============================================================================

class RadarrClient:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.headers = {"X-Api-Key": api_key}
    
    async def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Radarr."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.url}/api/v3/system/status", headers=self.headers)
                response.raise_for_status()
                return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    async def get_movies_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict]:
        """Find a movie in Radarr by TMDb ID."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.url}/api/v3/movie",
                    headers=self.headers
                )
                response.raise_for_status()
                movies = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch movies from Radarr: {e}")
            return None
    
        for movie in movies:
            if movie.get("tmdbId") == tmdb_id:
                logger.debug(f"Found movie in Radarr by TMDb ID: {movie.get('title')} (ID: {movie.get('id')})")
                return movie
    
        logger.debug(f"No Radarr entry found for TMDb ID: {tmdb_id}")
        return None
    
    async def get_movie_by_title_year(self, title: str, year: str) -> Optional[Dict]:
        """Find a movie in Radarr by title and year."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.url}/api/v3/movie",
                    headers=self.headers
                )
                response.raise_for_status()
                movies = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch movies from Radarr: {e}")
            return None
    
        # Search for matching title and year
        title_lower = title.lower().strip()
        year_int = int(year) if year and year.isdigit() else None
    
        for movie in movies:
            movie_title = movie.get("title", "").lower().strip()
            movie_year = movie.get("year")
        
            # Title match (exact or partial)
            title_match = (title_lower == movie_title or 
                        title_lower in movie_title or 
                        movie_title in title_lower)
        
            # Year match (if both have years)
            year_match = True
            if year_int and movie_year:
                year_match = (year_int == movie_year)
        
            if title_match and year_match:
                logger.debug(f"Found movie in Radarr: {movie.get('title')} ({movie.get('year')}) - ID: {movie.get('id')}")
                return movie
    
        logger.debug(f"No Radarr entry found for: {title} ({year})")
        return None
    
    
    async def get_movie_quality(self, movie_id: int) -> Optional[str]:
        """Get current quality of a movie's file."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.url}/api/v3/movie/{movie_id}",
                    headers=self.headers
                )
                response.raise_for_status()
                movie = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch movie {movie_id} from Radarr: {e}")
            return None
        
        movie_file = movie.get("movieFile")
        if not movie_file:
            logger.debug(f"Movie {movie_id} has no file")
            return None
        
        quality_wrapper = movie_file.get("quality", {})
        quality_obj = quality_wrapper.get("quality", {})
        quality_name = quality_obj.get("name", "Unknown")
        
        logger.debug(f"Current quality for movie {movie_id}: {quality_name}")
        return quality_name
    
    async def delete_movie_file(self, movie_id: int) -> Tuple[bool, str]:
        """Delete the movie file only (keep the movie entry in Radarr)."""
        try:
            # First get the movie file ID
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.url}/api/v3/movie/{movie_id}",
                    headers=self.headers
                )
                response.raise_for_status()
                movie = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch movie {movie_id}: {e}")
            return False, f"Failed to fetch movie: {e}"
        
        movie_file = movie.get("movieFile")
        if not movie_file:
            logger.info(f"Movie {movie_id} has no file to delete")
            return False, "No file to delete"
        
        file_id = movie_file.get("id")
        movie_title = movie.get("title", "Unknown")
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.delete(
                    f"{self.url}/api/v3/moviefile/{file_id}",
                    headers=self.headers
                )
                response.raise_for_status()
            
            logger.info(f"Successfully deleted file for '{movie_title}' (ID: {file_id})")
            return True, f"Deleted file for {movie_title}"
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(f"File already gone for {movie_title}")
                return True, "File already deleted"
            logger.error(f"Failed to delete file: {e}")
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            logger.error(f"Failed to delete file: {e}")
            return False, str(e)

# ============================================================================
# State Management (Last Run Tracking)
# ============================================================================

def load_last_run_state() -> Dict:
    """Load the last run state from JSON file."""
    state_file = LOG_DIR / "replacarr_last_run.json"
    
    if not state_file.exists():
        logger.debug("No previous run state found")
        return {"processed_movies": {}}
    
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
        logger.debug(f"Loaded last run state: {len(state.get('processed_movies', {}))} processed movies")
        return state
    except Exception as e:
        logger.warning(f"Failed to load last run state: {e}")
        return {"processed_movies": {}}

def save_last_run_state(processed_movies: Dict):
    """Save the current run state to JSON file."""
    state_file = LOG_DIR / "replacarr_last_run.json"
    
    state = {
        "last_run": datetime.now().isoformat(),
        "processed_movies": processed_movies
    }
    
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        logger.debug(f"Saved run state with {len(processed_movies)} processed movies")
    except Exception as e:
        logger.error(f"Failed to save run state: {e}")

def should_skip_movie(movie_id: int, processed_movies: Dict, skip_hours: int) -> Tuple[bool, str]:
    """Check if a movie should be skipped because it was recently processed."""
    if str(movie_id) not in processed_movies:
        return False, "Not processed before"
    
    last_processed = processed_movies[str(movie_id)]
    last_time = datetime.fromisoformat(last_processed)
    hours_since = (datetime.now() - last_time).total_seconds() / 3600
    
    if hours_since < skip_hours:
        return True, f"Processed {hours_since:.1f} hours ago (< {skip_hours})"
    
    return False, f"Processed {hours_since:.1f} hours ago (>= {skip_hours})"

# ============================================================================
# Main Logic
# ============================================================================

async def main():
    """Main execution function."""
    logger.info("=" * 50)
    logger.info("replacarr Starting")
    logger.info("=" * 50)
    
    # Load configuration
    config = get_config()
    
    # Initialize clients
    plex = PlexClient(config["plex_url"], config["plex_token"])
    radarr = RadarrClient(config["radarr_url"], config["radarr_api_key"])
    
    # Test connections
    plex_ok, plex_msg = await plex.test_connection()
    if not plex_ok:
        logger.error(f"Plex connection failed: {plex_msg}")
        sys.exit(1)
    logger.info(f"Plex: {plex_msg}")
    
    radarr_ok, radarr_msg = await radarr.test_connection()
    if not radarr_ok:
        logger.error(f"Radarr connection failed: {radarr_msg}")
        sys.exit(1)
    logger.info(f"Radarr: {radarr_msg}")
    
    # Load last run state
    state = load_last_run_state()
    processed_movies = state.get("processed_movies", {})
    
    # Step 1: Build TMDb mapping from Plex library (for accurate matching)
    logger.info(f"Step 1: Building TMDb mapping from Plex library...")
    tmdb_mapping = await plex.get_tmdb_mapping()

    # Step 2: Get recently played movies with pagination
    logger.info(f"Step 2: Fetching movies played in the last {config['recent_days']} days...")
    recently_played = await plex.get_recently_played_movies(config["recent_days"], tmdb_mapping)
    
    if not recently_played:
        logger.info("No recently played movies found")
        save_last_run_state(processed_movies)
        logger.info("replacarr Complete - Nothing to process")
        return
    
    # Step 3: Check each movie in Radarr
    logger.info("Step 3: Checking quality in Radarr...")
    
    movies_to_replace = []
    movies_checked = []
    
    for movie in recently_played:
        
        # Hybrid matching: try TMDb ID first, fall back to title/year
        radarr_movie = None

        # Strategy 1: Try TMDb ID (most accurate)
        if movie.get("tmdb_id"):
            logger.debug(f"Attempting TMDb lookup for '{movie['title']}': {movie['tmdb_id']}")
            radarr_movie = await radarr.get_movies_by_tmdb_id(movie["tmdb_id"])
            if radarr_movie:
                logger.debug(f"  ✓ Found by TMDb ID: {movie['tmdb_id']}")

        # Strategy 2: Fall back to title/year matching
        if not radarr_movie:
            logger.debug(f"Attempting title/year lookup for '{movie['title']} ({movie['year']})'")
            radarr_movie = await radarr.get_movie_by_title_year(movie["title"], movie["year"])
            if radarr_movie:
                logger.debug(f"  ✓ Found by title/year")
            else:
                logger.debug(f"  ✗ Not found in Radarr")

        if not radarr_movie:
            logger.debug(f"Movie '{movie['title']} ({movie['year']})' not found in Radarr - skipping")
            continue
        
        movie_id = radarr_movie.get("id")
        movie_title = radarr_movie.get("title", "Unknown")
        movie_year = radarr_movie.get("year", "")
        
        # Check if recently processed
        skip, skip_reason = should_skip_movie(movie_id, processed_movies, config["skip_hours"])
        if skip:
            logger.debug(f"Skipping '{movie_title} ({movie_year})' - {skip_reason}")
            continue
        
        # Get current quality
        current_quality = await radarr.get_movie_quality(movie_id)
        if not current_quality:
            logger.debug(f"No file found for '{movie_title}' - skipping")
            continue
        
        # Check if upgrade needed
        should_upgrade, reason = needs_upgrade(current_quality, config["desired_quality"])
        
        movie_info = {
            "movie_id": movie_id,
            "title": movie_title,
            "year": movie_year,
            "current_quality": current_quality,
            "play_count": movie["play_count"],
            "last_viewed": movie["last_viewed"],
            "should_upgrade": should_upgrade,
            "reason": reason
        }
        movies_checked.append(movie_info)
        
        if should_upgrade:
            logger.info(f"✓ '{movie_title} ({movie_year})' - {current_quality} → needs upgrade ({reason})")
            movies_to_replace.append(movie_info)
        else:
            logger.debug(f"✗ '{movie_title} ({movie_year})' - {current_quality} → {reason}")
    
    # Step 4: Apply replacements (up to max per run)
    logger.info(f"Step 4: Triggering replacements (max {config['max_replacements_per_run']} per run)...")
    
    replaced_count = 0
    failed_count = 0
    
    for movie in movies_to_replace[:config["max_replacements_per_run"]]:
        logger.info(f"  Deleting file for '{movie['title']}'...")
        success, message = await radarr.delete_movie_file(movie["movie_id"])
        
        if success:
            replaced_count += 1
            logger.info(f"    ✓ {message}")
            # Radarr will automatically search for replacement based on quality profile
        else:
            failed_count += 1
            logger.warning(f"    ✗ Failed: {message}")
        
        # Update processed state regardless of success/failure
        processed_movies[str(movie["movie_id"])] = datetime.now().isoformat()
        
        # Small delay between deletions
        await asyncio.sleep(1)
    
    # Step 5: Save results
    logger.info("Step 5: Saving run results...")
    save_last_run_state(processed_movies)
    
    # Save detailed results to JSON
    results_file = LOG_DIR / "replacarr_last_run.json"
    full_results = {
        "timestamp": datetime.now().isoformat(),
        "settings_used": {
            "desired_quality": config["desired_quality"],
            "recent_days": config["recent_days"],
            "max_replacements_per_run": config["max_replacements_per_run"],
            "skip_hours": config["skip_hours"]
        },
        "movies_checked": movies_checked,
        "movies_to_replace": [m for m in movies_to_replace[:config["max_replacements_per_run"]]],
        "summary": {
            "total_checked": len(movies_checked),
            "qualified_for_upgrade": len(movies_to_replace),
            "replaced": replaced_count,
            "failed": failed_count,
            "skipped_by_limit": max(0, len(movies_to_replace) - config["max_replacements_per_run"])
        }
    }
    
    try:
        with open(results_file, "w") as f:
            json.dump(full_results, f, indent=2, default=str)
        logger.info(f"Results saved to {results_file}")
    except Exception as e:
        logger.error(f"Failed to save detailed results: {e}")
    
    # Final summary
    logger.info("=" * 50)
    logger.info("replacarr Complete")
    logger.info(f"  Movies checked: {len(movies_checked)}")
    logger.info(f"  Qualified for upgrade: {len(movies_to_replace)}")
    logger.info(f"  Replaced: {replaced_count}")
    logger.info(f"  Failed: {failed_count}")
    logger.info("=" * 50)

# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())