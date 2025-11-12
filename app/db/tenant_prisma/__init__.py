from .prisma import Prisma

def get_prisma_client_for_tenant(db_url: str) -> Prisma:
    """
    テナントIDに対応するDBへのPrismaクライアントを返す
    """   
    return Prisma(
        datasources = {
            'db': {
                'url': db_url,
            } 
        }
    )