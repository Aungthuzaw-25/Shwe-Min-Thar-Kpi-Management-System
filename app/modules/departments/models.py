from sqlalchemy import Column, String, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database.base import BaseModel

class Department(BaseModel):
    __tablename__ = "departments"
    
    name = Column(String(100), unique=True, nullable=False)
    priority = Column(Integer, nullable=False, default=0, index=True)
    
    # 🌟 ERROR PREVENTION: ondelete="SET NULL" maintains referential integrity
    head_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="fk_dept_head_id"), nullable=True)
    
    head = relationship("User", foreign_keys=[head_id])
    user_departments = relationship("UserDepartment", back_populates="department", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="department", cascade="all, delete-orphan")

class UserDepartment(BaseModel):
    __tablename__ = "user_departments"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True)
    user_level = Column(String(2), nullable=False) # L0, L1, L2
    
    user = relationship("User", back_populates="user_departments")
    department = relationship("Department", back_populates="user_departments")