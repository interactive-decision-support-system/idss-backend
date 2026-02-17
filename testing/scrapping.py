import praw

reddit = praw.Reddit(client_id='YOUR_ID', client_secret='YOUR_SECRET', user_agent='LaptopScraper')

subreddits = ['SuggestALaptop', 'GamingLaptops', 'laptops']
dataset = []

for sub_name in subreddits:
    subreddit = reddit.subreddit(sub_name)
    # Search for "Request" flair to ensure the posts are actual buying queries
    for submission in subreddit.search('flair:"Request"', limit=40):
        if len(submission.selftext) > 100: # Filter out low-effort posts
            dataset.append({
                "subreddit": sub_name,
                "original": submission.selftext
            })