import { Link } from 'react-router-dom';
import { HomeIcon, InformationCircleIcon, BeakerIcon } from '@heroicons/react/24/outline';

const Navbar = () => {
  return (
    <nav className="bg-white shadow-lg">
      <div className="container mx-auto px-4">
        <div className="flex justify-between h-16">
          <div className="flex">
            <Link to="/" className="flex items-center">
              <HomeIcon className="h-6 w-6 text-gray-700" />
              <span className="ml-2 text-gray-700 font-medium">Web Scraper</span>
            </Link>
          </div>
          <div className="flex items-center space-x-4">
            <Link
              to="/login-guide"
              className="flex items-center px-3 py-2 rounded-md text-gray-700 hover:bg-gray-100"
            >
              <InformationCircleIcon className="h-6 w-6" />
              <span className="ml-2">Login Guide</span>
            </Link>
            <Link
              to="/api-test"
              className="flex items-center px-3 py-2 rounded-md text-gray-700 hover:bg-gray-100"
            >
              <BeakerIcon className="h-6 w-6" />
              <span className="ml-2">API Test</span>
            </Link>
          </div>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;