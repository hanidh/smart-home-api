## Running the Project with Docker

This project is containerized using Docker and Docker Compose for easy setup and deployment.

### Requirements
- **Docker** and **Docker Compose** installed on your system.
- The project uses **Python 3.13-slim** as the base image (see `Dockerfile`).
- All Python dependencies are managed via `requirements.txt` and installed in a virtual environment within the container.

### Environment Variables
- The Docker Compose file is set up to optionally use a `.env` file for environment variables. If your application requires specific environment variables, create a `.env` file in the project root and uncomment the `env_file: ./.env` line in `docker-compose.yml`.

### Build and Run Instructions
1. **(Optional)** If your application requires environment variables, create a `.env` file in the project root.
2. Build and start the application using Docker Compose:
   ```sh
   docker compose up --build
   ```
   This will build the image and start the `python-app` service.

### Special Configuration
- The application runs as a non-root user inside the container for improved security.
- The entrypoint runs `main.py` by default. If you need to change the entrypoint, modify the `CMD` in the `Dockerfile`.
- If your application needs to expose ports (e.g., for a web server), uncomment and configure the `ports:` section in `docker-compose.yml`.
- If your application depends on external services (like a database), add them to `docker-compose.yml` and set up `depends_on` as needed.

### Ports
- **No ports are exposed by default.**
- To expose ports (e.g., for a web API), edit the `docker-compose.yml` and add a `ports:` section under the `python-app` service. Example:
  ```yaml
  ports:
    - "8000:8000"
  ```

---

*Update this section if you add new services, environment variables, or change the entrypoint.*
