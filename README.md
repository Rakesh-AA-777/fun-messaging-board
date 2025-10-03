# Fun Message Board

A simple real-time message board built with Flask and Socket.IO.

## Features

- Real-time chat with nicknames and emoji decorations
- Messages stored in SQLite
- XSS-safe rendering and input validation
- Accessibility and keyboard navigation
- Loads only the most recent messages for performance

## Setup

1. **Install dependencies:**
    ```
    pip install -r requirements.txt
    ```

2. **Run the server (development):**
    ```
    python server.py
    ```

3. **Run the server (production/Render.com):**
    ```
    gunicorn -k eventlet -w 1 server:app
    ```

4. **Open your browser:**
    - Visit [http://localhost:5000](http://localhost:5000)

## Testing

Basic tests can be added in `tests/` (not included yet).

## Notes

- For production, set a strong `app.secret_key` in `server.py`.
- The app limits message history to the most recent 100 messages for scalability.

## Troubleshooting

If some files (like `index.html`, `styles.css`, or `test_server.py`) are not being added to your repository:
- Check your `.gitignore` file to ensure these files or their extensions are not listed.
- Make sure the files are inside your repository folder (now all files should be in the root, not in subfolders).
- Use `git status` to see if they are untracked or ignored.
- Use `git add .` to stage all files before committing.
