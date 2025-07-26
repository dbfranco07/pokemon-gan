
import csv

import requests
from urllib.parse import urlparse
import os

from selenium import webdriver
from selenium.webdriver.common.by import By

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)


def get_pokemon_names(driver):
    """Returns the list of pokemon names. The names will be used to get
    each individual image.
    """
    pokemon_path = 'https://pokemondb.net/sprites/'
    driver.get(pokemon_path)
    elem = driver.find_elements(By.CLASS_NAME, 'infocard')
    
    names = []
    for n in elem:
        name = n.get_attribute('href').split('/')[-1].replace(' ', '-')
        # name = n.text.lower().replace(' ', '-')
        names.append(name)

    logging.info(f'Extracted {len(names)} pokemon names')
  
    return names


def get_hrefs_per_pokemon(pokemon_name, driver):
    """Returns the list of hrefs of pokemon images.
    """
    pokemon_path = 'https://pokemondb.net/sprites/'
    driver.get(f'{pokemon_path}/{pokemon_name}')
    elem = driver.find_elements(By.CLASS_NAME, 'sprite-share-link')

    hrefs = []
    for e in elem:
        image_href = e.get_attribute('href')
        is_back_image = 'back' in image_href.lower()
        hrefs.append(image_href) if not is_back_image else None

    hrefs = list(set(hrefs))

    return hrefs


def download_image(href, to='images'):
    """Downloads the image based on the href.
    """
    response = requests.get(href)
    parsed_url = urlparse(href)
    image_name = parsed_url.path.replace('/', '_')[1:]
    image_path = os.path.join(os.getcwd(), to, image_name)

    with open(image_path, 'wb') as f:
        f.write(response.content)


def get_scraped_pokemon_names(checkpoint_file):
    """Reads the checkpoint_file and returns a list of already ran images.
    """
    scraped = []
    index_list = []
    try:
        with open(checkpoint_file, mode='r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                scraped.append(row['pokemon_name'])
                index_list.append(int(row['index']))
    except Exception as e:
        logging.info(e)

    logging.info(f'{len(scraped)} already scraped. Scraping other images.')
    last_index = max(index_list) if index_list else 0
    return last_index, scraped


def store_in_checkpoint(checkpoint_file, i_name_and_n):
    """Stores pokemon names inside the checkpoint file.
    """
    file_exist = os.path.exists(checkpoint_file)
    if file_exist:
        if os.path.getsize(checkpoint_file) == 0:
            file_empty = True
        else:
            file_empty = False
    else:
        file_empty = True
    
    with open(checkpoint_file, mode='a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=i_name_and_n[0].keys())
        writer.writeheader() if file_empty else None

        for row in i_name_and_n:
            writer.writerow(row)
    
    logging.info('Stored scraped data to checkpoint.')


def main():
    driver = webdriver.Chrome()
    checkpoint_dir = 'checkpoint'
    checkpoint_file = os.path.join(checkpoint_dir, 'scraped_images.csv')
    i = 0
    i_name_and_n = []

    pokemon_names = get_pokemon_names(driver=driver)
    last_index, scraped_names = get_scraped_pokemon_names(checkpoint_file)
    i = last_index + 1
    pokemon_names = [name for name in pokemon_names 
                     if name not in scraped_names]
    logging.info(f'Extracting remaining {len(pokemon_names)} pokemon names')

    try:
        for pokemon_name in pokemon_names:
            image_path = f'images/{i} - {pokemon_name}'

            if not os.path.exists(image_path):
                os.makedirs(image_path)
                logging.info(f'Created {image_path}')
            if not os.path.exists(checkpoint_dir):
                os.makedirs(checkpoint_dir)
                logging.info(f'Created {checkpoint_dir}')

            hrefs = get_hrefs_per_pokemon(pokemon_name=pokemon_name, 
                                          driver=driver)
            
            n_image = 0
            for href in hrefs:
                download_image(href, to=image_path)
                n_image += 1
            logging.info(f'{pokemon_name} - Downloaded {n_image} images')

            meta = {'index': i,
                    'pokemon_name': pokemon_name, 
                    'n_images': n_image}
            i_name_and_n.append(meta)
            i += 1

    except:
        logging.warning(f'Stopped at {pokemon_name}')
    finally:
        store_in_checkpoint(checkpoint_file=checkpoint_file,
                            i_name_and_n=i_name_and_n)

    driver.close()


if __name__ == '__main__':
    main()