from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import requests
from bs4 import BeautifulSoup
import random
import pandas as pd
import time
from fake_useragent import UserAgent
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
import undetected_chromedriver as uc
from urllib.parse import urlencode

app = FastAPI(
    title="Job Scraping API",
    description="API for scraping job listings from Indeed and LinkedIn",
    version="1.0.0",
    openapi_tags=[{
        "name": "Jobs",
        "description": "Endpoints for job scraping from different platforms"
    }]
)
# Initialize UserAgent for realistic headers
ua = UserAgent()

# Set up logging to console
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration for Indeed
PAGES_TO_SCRAPE = 1
BASE_URL = "https://in.indeed.com/jobs"

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# Request Models
# ==============================
class IndeedSearchParams(BaseModel):
    job_title: str = Field(..., example="Data Scientist")
    location: Optional[str] = Field(None, example="New York")
    days_posted: Optional[int] = Field(7, example=7)
    radius: Optional[int] = Field(50, example=50)
    job_type: Optional[str] = Field("fulltime", example="fulltime")
    sort_by: Optional[str] = Field("date", example="date")
    experience_level: Optional[str] = Field(None, example="entry_level")
    remote: Optional[str] = Field(None, example="true")

class LinkedInSearchParams(BaseModel):
    keywords: str = Field(..., example="Software Engineer")
    location: Optional[str] = Field(None, example="San Francisco")
    remote: Optional[str] = Field(None, example="true")
    experience_level: Optional[str] = Field(None, example="entry_level")
    job_type: Optional[str] = Field(None, example="full_time")
    time_posted: Optional[str] = Field(None, example="past_week")
    pages: int = Field(1, example=1)

# ==============================
# Response Models
# ==============================
class JobListing(BaseModel):
    platform: str
    job_id: str
    title: str
    company: str
    location: str
    salary: Optional[str]
    posted: Optional[str]
    job_url: str
    job_type: Optional[str]
    is_remote: bool
    experience_level: Optional[str]
    description: Optional[str]
    apply_link: Optional[str]

class StandardResponse(BaseModel):
    success: bool
    message: Optional[str]
    count: int
    data: List[Dict[str, Any]]

# ==============================
# Endpoints
# ==============================
@app.post(
    "/api/v1/jobs/indeed",
    response_model=StandardResponse,
    tags=["Jobs"],
    summary="Scrape Indeed jobs",
    status_code=status.HTTP_200_OK
)
async def scrape_indeed_jobs(params: IndeedSearchParams):
    """
    Scrape job listings from Indeed with various search filters
    
    - **job_title**: Job title to search for (required)
    - **location**: Geographic location for job search
    - **days_posted**: Number of days since posting (default: 7)
    - **radius**: Search radius in miles (default: 50)
    - **job_type**: Type of employment (fulltime, parttime, contract, etc.)
    - **sort_by**: Sort results by (date, relevance)
    - **experience_level**: Entry level, mid level, or senior level
    - **remote**: Filter remote jobs (true/false)
    """
    try:
        #filters = params.dict()
        filters = {
            'q': params.job_title,
            'l': params.location,
            'fromage': params.days_posted,
            'radius': params.radius,
            'jt': params.job_type,
            'sort': params.sort_by,
            'explvl': params.experience_level,
            'remote': params.remote
        }
        
        # Remove None values
        filters = {k: v for k, v in filters.items() if v is not None}
        
        job_listings = scrape_indeed(filters)
        
        return {
            "success": True,
            "message": f"Found {len(job_listings)} Indeed jobs",
            "count": len(job_listings),
            "data": [{"platform": "Indeed", **job} for job in job_listings]
        }
        
    except Exception as e:
        logger.error(f"Indeed scraping failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Indeed scraping failed: {str(e)}"
        )
def scrape_job_page(job_id):
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        response = requests.get(
            url,
            headers={'User-Agent': ua.random},
            timeout=10
        )
        if response.status_code != 200:
            logger.error(f"Failed to fetch job {job_id}: Status {response.status_code}")
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        job_data = {
            'job_id': job_id,
            'title': soup.find('h2', class_='top-card-layout__title').get_text(strip=True) if soup.find('h2', class_='top-card-layout__title') else None,
            'company': soup.find('a', class_='topcard__org-name-link').get_text(strip=True) if soup.find('a', class_='topcard__org-name-link') else None,
            'location': soup.find('span', class_='topcard__flavor--bullet').get_text(strip=True) if soup.find('span', class_='topcard__flavor--bullet') else None,
            'posted': soup.find('span', class_='posted-time-ago__text').get_text(strip=True) if soup.find('span', class_='posted-time-ago__text') else None,
            'applicants': soup.find('span', class_='num-applicants__caption').get_text(strip=True) if soup.find('span', class_='num-applicants__caption') else None,
            'url': f"https://www.linkedin.com/jobs/view/{job_id}",
            'company_url': soup.find('a', class_='topcard__org-name-link')['href'] if soup.find('a', class_='topcard__org-name-link') else None,
        }

        description_div = soup.find('div', class_='show-more-less-html__markup')
        if description_div:
            job_data['description'] = '\n'.join([p.get_text(strip=True) for p in description_div.find_all('p')])
        
        job_data.update(get_job_criteria(soup))
        
        return job_data
    
    except Exception as e:
        logger.error(f"Error scraping job {job_id}: {str(e)}")
        return None

def get_job_criteria(job_soup):
    criteria = {}
    items = job_soup.find_all('li', class_='description__job-criteria-item')
    for item in items:
        try:
            key = item.find('h3').get_text(strip=True).replace(' ', '_').lower()
            value = item.find('span').get_text(strip=True)
            criteria[key] = value
        except AttributeError:
            continue
    return criteria

def scrape_indeed(filters):
    print("Starting Indeed scraping with your filters:", filters)

    driver = get_driver()
    job_listings = []
    
    try:
        for page in range(PAGES_TO_SCRAPE):
            params = filters.copy()
            params['start'] = page * 10
            url = f"{BASE_URL}?{urlencode(params)}"

            driver.get(url)
            time.sleep(random.uniform(5, 10))
            human_like_interaction(driver)
            time.sleep(random.uniform(2, 4))

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            job_cards = soup.find_all('div', class_='job_seen_beacon')

            for card in job_cards:
                job_data = get_job_details(driver, card)
                job_listings.append(job_data)

            print(f"Page {page+1} processed with {len(job_cards)} jobs found")
            time.sleep(random.uniform(8, 15))

    except Exception as e:
        print(f"Scraping interrupted: {str(e)}")
    finally:
        driver.quit()
    
    print(f"Total jobs collected: {len(job_listings)}")
    return job_listings

def get_driver():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(100,120)}.0.0.0 Safari/537.36")
    options.add_argument("--window-size=1920,1080")
    #options.add_argument("--headless")
    
    driver = uc.Chrome(use_subprocess=True, options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def human_like_interaction(driver):
    """Simulates human-like interactions with safe mouse movements"""
    actions = ActionChains(driver)
    actions.move_by_offset(10, 10).perform()
    time.sleep(random.uniform(0.5, 1.5))
    
    for _ in range(random.randint(2, 4)):
        x_offset = random.randint(-50, 50)
        y_offset = random.randint(-50, 50)
        try:
            actions.move_by_offset(x_offset, y_offset).perform()
            actions.pause(random.uniform(0.5, 1.5))
        except Exception:
            pass

    for _ in range(random.randint(3, 5)):
        try:
            if random.choice([True, False]):
                driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
            else:
                driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_UP)
            time.sleep(random.uniform(0.5, 2))
        except Exception:
            pass

def get_job_details(driver, job_card):
    """Extracts detailed information from a job card"""
    details = {
        'job_id': '',
        'title': '',
        'company': '',
        'location': '',
        'salary': '',
        'posted': '',
        'job_url': '',
        'company_rating': '',
        'job_type': '',
        'shift':'',
        'benefits': '',
        'is_remote': False,
        'is_urgent': False,
        'job_snippet': '',
        'experience_level': '',
        'work_model': 'On-site',
        'image_link':'',
        'apply_link':'',
    }

    try:
        job_link = job_card.find('a', class_='jcs-JobTitle')
        if job_link:
            details['job_id'] = job_link.get('data-jk', '')
            details['job_url'] = f"https://in.indeed.com/viewjob?jk={details['job_id']}"
            details['title'] = job_link.get_text(strip=True)

        driver.get(details['job_url'])
        time.sleep(random.uniform(3, 6))
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        details['company'] = soup.find("meta", {"property": "og:description"})["content"] if soup.find("meta", {"property": "og:description"}) else None
        details['location'] = soup.find("title").text.split(" - ")[1] if soup.find("title") else None
        details['image_link'] = soup.find("meta", {"property": "og:image"})["content"] if soup.find("meta", {"property": "og:image"}) else None
        
        details['is_remote'] = bool(soup.find('div', class_='remote-badge'))
        
        job_description = soup.find("div", class_="jobsearch-JobComponent-description")
        details['job_snippet'] = job_description.get_text(strip=True) if job_description else "Not Provided"

        pay_tag = soup.find(string=lambda text: "₹" in text if text else False)
        details['salary'] = pay_tag.strip() if pay_tag else "Not mentioned"
        
        job_type_options = ["Full-time", "Part-time", "Internship", "Permanent", "Contract"]
        job_types = [jt for jt in job_type_options if soup.find(string=jt)]
        details['job_type'] = ", ".join(job_types) if job_types else "Not mentioned"

        shift_options = ["Day shift", "Night shift", "Rotational shift", "Fixed shift"]
        shifts = [s for s in shift_options if soup.find(string=s)]
        details['shift'] = ", ".join(shifts) if shifts else "Not mentioned"

        benefits_header = soup.find(string=lambda text: "Benefits" in text if text else False)
        details['benefits'] = benefits_header.find_next("ul").text if benefits_header else "Not mentioned"
        
        apply_link_meta = soup.find("meta", {"property": "og:url"})
        details['apply_link'] = apply_link_meta["content"] if apply_link_meta else "Not found"
    except Exception:
        pass
    
    return details

@app.post(
    "/api/v1/jobs/linkedin",
    response_model=StandardResponse,
    tags=["Jobs"],
    summary="Scrape LinkedIn jobs",
    status_code=status.HTTP_200_OK
)
async def scrape_linkedin_jobs(params: LinkedInSearchParams):
    """
    Scrape job listings from LinkedIn with various search filters
    
    - **keywords**: Job search keywords (required)
    - **location**: Geographic location for job search
    - **remote**: Filter remote jobs
    - **experience_level**: Experience level filter
    - **job_type**: Employment type filter
    - **time_posted**: Time since posting filter
    - **pages**: Number of pages to scrape (default: 1)
    """
    try:
        job_data = await scrape_jobs_linkedin(params)
        
        return {
            "success": True,
            "message": f"Found {len(job_data)} LinkedIn jobs",
            "count": len(job_data),
            "data": [{"platform": "LinkedIn", **job} for job in job_data]
        }
        
    except Exception as e:
        logger.error(f"LinkedIn scraping failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LinkedIn scraping failed: {str(e)}"
        )

# ==============================
# Helper Functions (Keep existing scraping logic unchanged)
# ==============================
# ... [Keep all your existing scraping helper functions unchanged] ...

# Update the original LinkedIn endpoint to be an internal function
async def scrape_jobs_linkedin(params: LinkedInSearchParams):
    settings = {
        "timeout": 10,
        "delay": (3, 6),
        "max_retries": 3
    }

    job_ids = []
    for page in range(params.pages):
        try:
            request_params = params.dict()
            request_params["start"] = page * 25
            
            response = requests.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                params=request_params,
                headers={'User-Agent': ua.random},
                timeout=settings["timeout"]
            )

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                jobs = soup.find_all('li')
                for job in jobs:
                    if job_id := job.find('div', {'class': 'base-card'}).get('data-entity-urn', '').split(':')[-1]:
                        job_ids.append(job_id)
                time.sleep(random.uniform(*settings["delay"]))
            else:
                logger.error(f"Failed to fetch jobs: Status {response.status_code}")
                raise HTTPException(status_code=response.status_code, detail="Failed to fetch jobs")
                
        except Exception as e:
            logger.error(f"Error during job scraping: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    jobs_data = []
    for job_id in job_ids:
        for attempt in range(settings["max_retries"]):
            job_info = scrape_job_page(job_id)
            if job_info:
                jobs_data.append(job_info)
                logger.info(f"Successfully scraped job {job_id}")
                break
            time.sleep(random.uniform(*settings["delay"]))
        else:
            logger.warning(f"Failed to scrape job {job_id} after {settings['max_retries']} attempts")

    return jobs_data
